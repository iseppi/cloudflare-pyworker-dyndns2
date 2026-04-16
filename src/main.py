from workers import WorkerEntrypoint, Response


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("cloudflare-pyworker-dyndns2 worker is running")
