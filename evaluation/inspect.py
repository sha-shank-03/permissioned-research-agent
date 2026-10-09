"""Independent, memory-only surface inspection; does not use runtime guards."""

import copy


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from strings(item)
    elif hasattr(value, "__dict__"):
        yield from strings(value.__dict__)


class SurfaceInspector:
    def __init__(self, store, fixture):
        self.store, self.fixture = store, fixture
        self.events = []
        self.violations = []

    def __call__(self, surface, record):
        # Independent ACL calculation from raw rows/sets, not source_for or
        # authorized_sources. Label data and this code never reach actor tools.
        connection = self.store.connection
        epoch = connection.execute("SELECT epoch FROM control_epoch").fetchone()[0]
        groups = {row[0] for row in connection.execute("SELECT group_name FROM memberships WHERE principal=?", (record["principal"],))}
        grants = {}
        for resource, group in connection.execute("SELECT resource,group_name FROM grants"):
            grants.setdefault(resource, set()).add(group)
        current = {row[0]: (row[1], row[2], row[3], row[4]) for row in connection.execute(
            "SELECT o.object_id,o.acl_resource,o.title,v.body,o.current_revision FROM objects o JOIN versions v ON v.object_id=o.object_id AND v.revision=o.current_revision")}
        flattened = list(strings(record["payload"]))
        visible_bodies = [row[2] for row in current.values() if groups & grants.get(row[0], set())]
        forbidden = []
        for source in self.fixture["sources"]:
            row = current.get(source["object_id"])
            if row is None or not (groups & grants.get(row[0], set())):
                forbidden.extend([source["object_id"], source["title"], source["body"]])
                forbidden.extend(line for line in source["body"].splitlines() if line and
                                 not any(line in body for body in visible_bodies))
            elif row[2] != source["body"]:
                forbidden.append(source["body"])
                forbidden.extend(line for line in source["body"].splitlines() if line and
                                 not any(line in body for body in visible_bodies))
        if record["epoch"] != epoch:
            self.violations.append({"surface": surface, "kind": "epoch_mismatch"})
        for protected in forbidden:
            if protected and any(protected in text for text in flattened):
                self.violations.append({"surface": surface, "kind": "forbidden_content"})
        self.events.append((surface, copy.deepcopy(record)))

    def check_audit(self):
        rows = [dict(row) for row in self.store.connection.execute("SELECT * FROM audit_events")]
        receipts = [dict(row) for row in self.store.connection.execute("SELECT * FROM run_receipts")]
        attempts = [dict(row) for row in self.store.connection.execute("SELECT * FROM run_attempts")]
        bindings = [dict(row) for row in self.store.connection.execute("SELECT * FROM request_bindings")]
        content = list(strings([rows, receipts, attempts, bindings]))
        for source in self.fixture["sources"]:
            for protected in (source["object_id"], source["title"], source["body"]):
                if any(protected in text for text in content):
                    self.violations.append({"surface": "audit", "kind": "source_content"})
        return len(rows)
