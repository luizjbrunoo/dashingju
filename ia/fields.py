"""Campo vetorial: pgvector no PostgreSQL; texto no SQLite de testes."""

from pgvector.django import VectorField


class RagVectorField(VectorField):
    """VectorField(1536) em PostgreSQL. SQLite persiste o literal textual."""

    def deconstruct(self):
        name, _path, args, kwargs = super().deconstruct()
        return name, "ia.fields.RagVectorField", args, kwargs

    def db_type(self, connection):
        if connection.vendor != "postgresql":
            return "text"
        return super().db_type(connection)
