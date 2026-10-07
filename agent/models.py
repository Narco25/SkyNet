from pydantic import BaseModel, Field


class Task(BaseModel):
    id: int
    title: str
    description: str
    file_path: str
    acceptance_criteria: str


class TaskPlan(BaseModel):
    project_name: str
    summary: str
    tasks: list[Task] = Field(default_factory=list)


class FileChangeProposal(BaseModel):
    explanation: str
    file_path: str
    full_file_content: str
