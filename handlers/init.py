def register_all(dp):
    from . import start, admin, channel, dashboard

    for name, module in (
        ("start", start),
        ("admin", admin),
        ("channel", channel),
        ("dashboard", dashboard),
    ):
        if not hasattr(module, "router"):
            raise ImportError(f"handlers/{name}.py must define `router = Router()`")

    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(channel.router)
    dp.include_router(dashboard.router)
