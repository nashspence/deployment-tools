"""Connection-owned transactions include DDL and Alembic version updates."""

from alembic import context


def run() -> None:
    connection = context.config.attributes.get("connection")
    if (
        connection is None
        or not connection.in_transaction()
        or not connection.connection.driver_connection.in_transaction
    ):
        raise RuntimeError("an explicit transaction-owned connection is required")
    context.configure(connection=connection, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


# Import-safe for installed-package discovery; Alembic installs this context itself.
if getattr(context, "config", None) is not None:
    run()
