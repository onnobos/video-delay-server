from pydantic import BaseModel
from src.config import (
    DELAY_SECONDS,
    FPS,
    WIDTH,
    HEIGHT,
    SOURCE_NAME,
    CAM_INDEX_PRIMARY,
    CAM_INDEX_SECONDARY
)

class StreamSettings(BaseModel):
    delay_seconds: int = DELAY_SECONDS
    fps: int = FPS
    width: int = WIDTH
    height: int = HEIGHT
    source_name: str = SOURCE_NAME
    cam_index_primary: int = CAM_INDEX_PRIMARY
    cam_index_secondary: int = CAM_INDEX_SECONDARY

    @property
    def buffer_size(self) -> int:
        return self.delay_seconds * self.fps
