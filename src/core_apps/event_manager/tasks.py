import logging
import os
import json

# Django
from django.conf import settings
from celery_progress.backend import ProgressRecorder

# Celery
from celery import shared_task

# S3
import boto3
from botocore.exceptions import ClientError

# Locsl
from core_apps.event_manager.models import VideoMetaData
from core_apps.event_manager.s3_utils import get_s3_client, S3UploadProgressRecorder
from core_apps.event_manager.producers import s3_metadata_publisher_mq


logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3)
def upload_video_to_s3(
    self,
    local_video_filepath,
    s3_file_key,
    video_metadata_id,
    video_file_format,
    video_filename_with_extention,
):
    """Celery Task to Upload Raw Movio Videos to Process By Movio-Worker-Serivce"""

    s3_client = get_s3_client()

    def generate_chain_result(
        success: bool = True,
        exception: str = None,
        error_message: str = None,
        success_message: str = None,
        s3_presigned_url: str = None,
    ):
        """To generate the chain result"""

        return {
            "success": success,
            "exception": exception,
            "success_message": success_message,
            "error_message": error_message,
            "local_video_filepath": local_video_filepath,
            "s3_file_key": s3_file_key,
            "s3_presigned_url": s3_presigned_url,
            "video_id": video_obj.id if video_obj else None,
            "video_filename_with_extention": video_filename_with_extention,
        }

    try:
        video_obj = VideoMetaData.objects.get(id=video_metadata_id)
    except VideoMetaData.DoesNotExist:
        logger.error(
            f"\n\n[XX Video Upload S3 ERROR XX]: Video Meta Data with ID: {video_metadata_id} does not exist."
        )
        return generate_chain_result(
            success=False,
            exception="DoseNotExist",
            error_message="video-upload-failed-video-db-entry-not-exists",
        )

    def update_video_metadata_status(success=False):
        """To update the video metadata status"""

        video_obj.is_processing = False
        video_obj.is_processing_completed = success
        video_obj.is_processing_failed = not success
        video_obj.save()

    # callback function to record the progress of the upload
    # progress_recorder = S3UploadProgressRecorder(filepath=local_video_filepath, task=self)

    try:
        s3_client.upload_file(
            Filename=local_video_filepath,
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Key=s3_file_key,
            ExtraArgs={
                "ContentType": f"{video_file_format}",
            },
            # Callback=progress_recorder,  # callabck not working: requires task_id, unknown error
        )

        s3_presigned_url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.AWS_STORAGE_BUCKET_NAME, "Key": s3_file_key},
            ExpiresIn=3600 * 24,  # for 24 hours validity
        )
        update_video_metadata_status(success=True)

        logger.info(
            f"\n\n[=> Video Upload S3 SUCCESS]: Video Upload to S3 Successful: {local_video_filepath}"
        )
        return generate_chain_result(
            success=True,
            success_message=f"Video Upload to S3 Successful: {local_video_filepath}",
            s3_presigned_url=s3_presigned_url,
        )

    except FileNotFoundError as e:
        logger.exception(
            f"\n\n[XX Video Upload S3 ERROR XX]: The Local VIdeo File: {local_video_filepath} was not Found.\nException: {str(e)}"
        )
        update_video_metadata_status(success=False)

        return generate_chain_result(
            success=False, exception="FileNotFound", error_message="file-not-found"
        )

    except ClientError as e:
        logger.warning(
            f"\n\n[XX Video Upload S3 ERROR XX]: S3 Client Error.\nException: {str(e)}\nRetrying to upload: {local_video_filepath}"
        )
        # Exponential backoff
        if self.request.retries < self.max_retries:
            retry_in = 2**self.request.retries
            logger.warning(
                f"\n\n[## VIDEO S3 UPLOAD WARNING ]: ClientError: The Local Video {local_video_filepath} Couldn't be Uploaded.\nRetrying in: {retry_in}."
            )
            raise self.retry(exc=e, countdown=retry_in)
        else:
            update_video_metadata_status(success=False)

            return generate_chain_result(
                success=False,
                exception="ClientError",
                error_message="client-error-retry-exhausted",
            )

    except Exception as e:
        logger.exception(
            f"\n\n[XX Video Upload S3 ERROR XX]: Unexpected Error Occurred. Local Video couldn't be uploaded to S3.\nException: {str(e)}"
        )
        update_video_metadata_status(success=False)

        return generate_chain_result(
            success=False, exception="GeneralException", error_message=f"{str(e)}"
        )


@shared_task
def delete_local_video_file_after_s3_upload(preprocessing_result):
    "'''Delete Local Video File from Local System aftr S3 Upload" ""

    def generate_chain_result(
        preprocessing_result: dict,
        success: bool = True,
        exception: str = None,
        error_message: str = None,
        success_message: str = None,
    ):
        """To generate the chain result"""

        preprocessing_result["local_video_delete_success"] = success
        preprocessing_result["local_video_delete_exception"] = exception
        preprocessing_result["local_video_delete_error_message"] = error_message
        preprocessing_result["local_video_delete_success_message"] = success_message
        return preprocessing_result

    try:
        if preprocessing_result["success"] is True:

            os.remove(preprocessing_result["local_video_filepath"])
            logger.info(
                f"\n\n[=>  Video DELETE SUCCESS]: Local Video File Deleted Successfully."
            )
            return generate_chain_result(
                preprocessing_result=preprocessing_result,
                success=True,
                success_message="local-video-delete-success.",
            )
        else:
            return preprocessing_result
    except FileNotFoundError as e:
        logger.warning(
            f"\n\n[XX Video DELETE ERROR XX]: Local Video File Couldn't be Deleted.\nFileNotFound Exception: {str(e)}"
        )
        return generate_chain_result(
            preprocessing_result=preprocessing_result,
            success=False,
            exception="FileNotFound",
            error_message="video-file-not-found.",
        )
    except Exception as e:
        logger.exception(
            f"\n\n[XX Video DELETE ERROR XX]: Local Video File Couldn't be Deleted.\nGeneral Exception: {str(e)}"
        )
        return generate_chain_result(
            preprocessing_result=preprocessing_result,
            success=False,
            exception="GeneralException",
            error_message="video-deletion-unexpected-error",
        )


@shared_task
def publish_s3_metadata_to_mq(preprocessing_result, user_data):
    """
    Publish the S3 Metadata to MQ
    """

    def generate_chain_result(
        preprocessing_result: dict,
        success: bool = True,
        exception: str = None,
        error_message: str = None,
        success_message: str = None,
    ):
        """To generate the chain result"""

        preprocessing_result["mq_publish_success"] = success
        preprocessing_result["mq_publish_exception"] = exception
        preprocessing_result["mq_publish_error_message"] = error_message
        preprocessing_result["mq_publish_success_message"] = success_message
        return preprocessing_result

    if preprocessing_result["success"] is True:
        s3_file_key = preprocessing_result["s3_file_key"]
        s3_file_url = f"https://{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com/{s3_file_key}"

        s3_presigned_url = preprocessing_result["s3_presigned_url"]

        mq_data = {
            "video_id": str(preprocessing_result["video_id"]),
            "s3_file_key": s3_file_key,  # the s3 file key is the s3 file name
            "s3_file_url": s3_file_url,  # the open url
            "s3_presigned_url": s3_presigned_url,
            "video_filename_with_extention": preprocessing_result[
                "video_filename_with_extention"
            ],
            "user_data": user_data,
        }

        mq_data = json.dumps(mq_data)

        # Publishing teh S3 URL na dthe Video_ID to the RabbitMQ
        published, message = s3_metadata_publisher_mq.publish_data(s3_data=mq_data)
        if published:
            return generate_chain_result(
                preprocessing_result=preprocessing_result,
                success=True,
                success_message=message,
            )
        else:
            return generate_chain_result(
                preprocessing_result=preprocessing_result,
                success=False,
                exception="MQPublishError",
                error_message=message,
            )
    else:
        return preprocessing_result
