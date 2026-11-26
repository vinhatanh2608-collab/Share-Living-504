import asyncio


class Manager:
    def __init__(self):
        self.clients = {}

    def attach(self, user):
        queue = asyncio.Queue(maxsize=64)
        self.clients.setdefault(user, set()).add(queue)
        return queue

    def detach(self, user, queue):
        group = self.clients[user]
        group.discard(queue)
        if not group:
            del self.clients[user]

    def publish(self, event, audience=None):
        for user, queues in self.clients.items():
            if audience is None or user in audience:
                for queue in queues:
                    if queue.full():
                        while not queue.empty():
                            queue.get_nowait()
                        queue.put_nowait({"type": "resync"})
                    else:
                        queue.put_nowait(event)
