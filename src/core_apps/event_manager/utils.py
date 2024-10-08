import os
import uuid
import logging


from django.conf import settings
from celery_progress.backend import ProgressRecorder

logger = logging.getLogger(__name__)


def save_video_local_storage(request):
    """A util function to save the vidoe in local storage.
    
    Input:
        request: A Django http.request object.

    Return:
        A dict of video file metadata.
    """

    try:

        raw_video_file = request.FILES["video"]
        title = request.data.get("title")
        description = request.data.get("description")
        duration = request.data.get("duration")

        video_file_basename = os.path.splitext(raw_video_file.name)[0]
        video_file_extention = os.path.splitext(raw_video_file.name)[1]  

        video_filename_without_extention = f"{uuid.uuid4()}__{video_file_basename}"  
        video_filename_with_extention = f"{video_filename_without_extention}{video_file_extention}"

        # BASE_DIR/movio-local-video-files/tmp-movio-videos/
        if not os.path.exists(settings.TEMP_LOCAL_VIDEO_DIR):
            os.mkdir(settings.TEMP_LOCAL_VIDEO_DIR)

        local_video_path_without_extention = os.path.join(
            settings.TEMP_LOCAL_VIDEO_DIR,
            f"{video_filename_without_extention}",
        )

        local_video_path_with_extention = os.path.join(
            settings.TEMP_LOCAL_VIDEO_DIR,
            f"{video_filename_without_extention}{video_file_extention}",
        )

        with open(local_video_path_with_extention, "wb+") as video_file_destination:
            for chunk in raw_video_file.chunks():
                video_file_destination.write(chunk)

        """ Example Payload (added from middleware):
        {'token_type': 'access', 'exp': 1728897938, 'iat': 1726305938, 'jti': '2fa683f424ea4e219b5dc8c28d542e82', 
        'user_id': '440bb3d2-8482-4924-b55e-983904dff20f', 
        'user_data': {'first_name': 'Mahboob', 'last_name': 'Alam', 'username': 'i_yurious_3', 'email': 'iammahboob.a.3@gmail.com', 'phone_number': None}}
        """

        # To save in db
        user_id = request.payload.get("user_id")
        user_data = request.payload.get("user_data")

        db_data = {
            "custom_video_title": video_filename_without_extention, 
            "title": title,
            "description": description,
            "duration": duration,
            "user_id": user_id,
            "email": user_data.get("email"),
            "phone_number": user_data.get("phone_number"),
        }

        logger.info(
            f"\n[=>Save Video Local SUCCESS]: Video save to local storage is Successful."
        )

        return {
            "status": True,
            "video_file_extention": video_file_extention,
            "video_filename_with_extention": video_filename_with_extention, 
            "video_filename_without_extention": video_filename_without_extention,
            "local_video_path_with_extention": local_video_path_with_extention,
            "local_video_path_without_extention": local_video_path_without_extention,
            "db_data": db_data,
        }
    except Exception as e:
        logger.error(
            f"\n[XX Save Video Local ERROR XX]: Video save to local and segment dirs for mp4 and mov Creation Failed.\nEXCEPTION: {str(e)}"
        )
        return {"status": False, "error": str(e)}


def validate_video_file(video_file_size, video_file_format):
    '''Validate the max file size and the allowed video file format'''
    
    if video_file_size > settings.MAX_VIDEO_FILE_SIZE:
        return False, f"Video file size exceed the maximum limit of {settings.MAX_VIDEO_FILE_SIZE / (1024 *1024)} MB" # 100 MB
    
    if video_file_format not in settings.ALLOWED_VIDEO_FILE_FORMATS:
        return False, f"Invalid video file format. Only {', '.join(settings.ALLOWED_VIDEO_FILE_FORMATS)} are supported."
    
    return True, None
