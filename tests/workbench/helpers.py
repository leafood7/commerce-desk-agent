"""Explicit in-process substitutes; production never selects them as a fallback."""

from workbench.domain import get_order, order_view
from workbench.tool_contracts import verify_grant


class LocalTestGateway:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings

    async def call(self, name, arguments, token):
        with self.database.sessions() as session:
            user, scopes = verify_grant(session, token, self.settings)
            if name == "get_order_status":
                return {
                    "status": "found",
                    "order": order_view(
                        session, get_order(session, user, arguments["order_number"])
                    ),
                }
            raise RuntimeError("Test gateway has no implicit knowledge response")


class MemoryIndex:
    revision = "test-only"

    def __init__(self):
        self.rows = {}

    def upsert(self, organization_id, version_id, chunks, heartbeat):
        for chunk in chunks:
            self.rows[chunk["id"]] = {
                **chunk,
                "organization_id": organization_id,
                "version_id": version_id,
            }
        heartbeat()

    def query(self, organization_id, version_ids, query, limit):
        from business.rag_retrieval import tokenize

        terms = set(tokenize(query))
        rows = []
        for row in self.rows.values():
            if row["organization_id"] == organization_id and row["version_id"] in version_ids:
                score = len(terms & set(tokenize(row["content"])))
                rows.append({"id": row["id"], "distance": 0.1 if score else 1.0})
        return sorted(rows, key=lambda row: row["distance"])[:limit]
