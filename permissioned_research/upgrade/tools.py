"""Session-local canonical handles for identical already-authorized sources."""
from ..connectors import ConnectorTools


class ScalarTools(ConnectorTools):
    def _observe(self, surface, payload):
        if surface == 'tool_output' and type(payload) is dict and 'hits' in payload:
            # The parent has filtered ACLs and minted handles inside its gate.
            # Compare actual trusted Source objects, never title/excerpt guesses.
            canonical = {}
            for handle, evidence in self.evidence.items():
                canonical.setdefault(evidence.source, handle)
            for hit in payload['hits']:
                issued = hit['handle']
                retained = canonical[self.evidence[issued].source]
                if retained != issued:
                    hit['handle'] = retained
                    del self.evidence[issued]
            # The observer and returned result see the same handle projection.
            # A new tool session/epoch has an empty evidence map and fresh IDs.
        super()._observe(surface, payload)
