from typing import Optional
from pydantic import BaseModel, Field
from src.config import (
    DELAY_SECONDS,
    FPS,
    WIDTH,
    HEIGHT,
    CAM_INDEX_PRIMARY,
    CAM_INDEX_SECONDARY,
    SHOW_TIMESTAMP,
    SHOW_FPS,
    SHOW_CAMERA_NAME,
)

class StreamSettings(BaseModel):
    delay_seconds: int = Field(default=DELAY_SECONDS, ge=1, le=120)
    fps: int = Field(default=FPS, ge=5, le=60)
    width: int = Field(default=WIDTH, ge=320, le=3840)
    height: int = Field(default=HEIGHT, ge=240, le=2160)
    
    # Camera selection
    cam_index: Optional[int] = None
    cam_name: Optional[str] = None
    cam_index_primary: Optional[int] = CAM_INDEX_PRIMARY
    cam_index_secondary: Optional[int] = CAM_INDEX_SECONDARY

    # Video Overlays
    show_timestamp: bool = SHOW_TIMESTAMP
    show_fps: bool = SHOW_FPS
    show_camera_name: bool = SHOW_CAMERA_NAME

    @property
    def buffer_size(self) -> int:
        return max(1, self.delay_seconds * self.fps)

    @property
    def active_camera_index(self) -> Optional[int]:
        if self.cam_index is not None:
            return self.cam_index
        return self.cam_index_primary
