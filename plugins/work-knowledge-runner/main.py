from langbot_plugin.api.definition.plugin import BasePlugin


class WorkKnowledgePlugin(BasePlugin):
    """Local knowledge integration; all invocation data stays in the run context."""

    async def initialize(self):
        pass
