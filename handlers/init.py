def register_all(dp):
    from . import start, admin, channel, dashboard
    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(channel.router)
    dp.include_router(dashboard.router)
