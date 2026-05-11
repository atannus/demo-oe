from pydantic import BaseModel


class PositionBody(BaseModel):
    x: float
    y: float


class PartitionBody(BaseModel):
    active: bool
    mode: str


class PartitionConfigBody(BaseModel):
    autoMode: str
