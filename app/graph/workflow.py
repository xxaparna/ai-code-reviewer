from app.graph.nodes import (
    architecture_node,
    bug_node,
    merge_node,
    performance_node,
    security_node,
    style_node,
    summary_node,
)


class ReviewWorkflow:

    def __init__(self):
        self.nodes = [
            bug_node,
            security_node,
            style_node,
            performance_node,
            architecture_node,
            merge_node,
            summary_node,
        ]

    def run(self, state):

        for node in self.nodes:
            state = node(state)

        return state