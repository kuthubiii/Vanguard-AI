import pytest
from sqlalchemy import inspect, text


@pytest.mark.postgres
def test_postgres_connection(postgres_engine):
    with postgres_engine.connect() as connection:
        database_name = connection.execute(
            text("SELECT current_database()")
        ).scalar_one()

    assert database_name == "vanguard_ai_test"

    inspector = inspect(postgres_engine)

    assert "refresh_tokens" in inspector.get_table_names()
    assert "users" in inspector.get_table_names()