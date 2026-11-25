from src.models import audit, cases, library, reviews, runs, users  # Register the tables.
from src.models.base import metadata


def upgrade(connection):
    # Version 2 adds the shared maintenance library. create_all is additive and
    # leaves every existing workbench table untouched.
    metadata.create_all(connection)
