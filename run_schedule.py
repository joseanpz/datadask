import atexit
import logging
import gc
import os
import re
import shutil
import sys
import tempfile
import warnings

from tornado.ioloop import IOLoop
from distributed import Scheduler
from distributed.security import Security
from distributed.cli.utils import install_signal_handlers
from distributed.proctitle import (
    enable_proctitle_on_children,
    enable_proctitle_on_current,
)

logger = logging.getLogger("distributed.scheduler")


def main(
    host=None,
    port=None,
    bokeh_port=None,
    show=True,
    dashboard=True,
    bokeh=None,
    dashboard_prefix=None,
    use_xheaders=False,
    pid_file="",
    local_directory="",
    tls_ca_file=None,
    tls_cert=None,
    tls_key=None,
    dashboard_address="0.0.0.0:8788",
    **kwargs
):
    g0, g1, g2 = gc.get_threshold()  # https://github.com/dask/distributed/issues/1653
    gc.set_threshold(g0 * 3, g1 * 3, g2 * 3)

    enable_proctitle_on_current()
    enable_proctitle_on_children()

    if bokeh_port is not None:
        warnings.warn(
            "The --bokeh-port flag has been renamed to --dashboard-address. "
            "Consider adding ``--dashboard-address :%d`` " % bokeh_port
        )
        dashboard_address = bokeh_port
    if bokeh is not None:
        warnings.warn(
            "The --bokeh/--no-bokeh flag has been renamed to --dashboard/--no-dashboard. "
        )
        dashboard = bokeh

    if port is None and (not host or not re.search(r":\d", host)):
        port = 8786

    sec = Security(
        **{
            k: v
            for k, v in [
                ("tls_ca_file", tls_ca_file),
                ("tls_scheduler_cert", tls_cert),
                ("tls_scheduler_key", tls_key),
            ]
            if v is not None
        }
    )

    if not host and (tls_ca_file or tls_cert or tls_key):
        host = "tls://"

    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

        def del_pid_file():
            if os.path.exists(pid_file):
                os.remove(pid_file)

        atexit.register(del_pid_file)

    local_directory_created = False
    if local_directory:
        if not os.path.exists(local_directory):
            os.mkdir(local_directory)
            local_directory_created = True
    else:
        local_directory = tempfile.mkdtemp(prefix="scheduler-")
        local_directory_created = True
    if local_directory not in sys.path:
        sys.path.insert(0, local_directory)

    if sys.platform.startswith("linux"):
        import resource  # module fails importing on Windows

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        limit = max(soft, hard // 2)
        resource.setrlimit(resource.RLIMIT_NOFILE, (limit, hard))

    loop = IOLoop.current()
    logger.info("-" * 47)

    scheduler = Scheduler(
        loop=loop,
        security=sec,
        host=host,
        port=port,
        dashboard_address=dashboard_address if dashboard else None,
        service_kwargs={"dashboard": {"prefix": dashboard_prefix}},
        **kwargs
    )
    logger.info("Local Directory: %26s", local_directory)
    logger.info("-" * 47)

    install_signal_handlers(loop)

    async def run():
        await scheduler
        await scheduler.finished()

    try:
        loop.run_sync(run)
    finally:
        scheduler.stop()
        if local_directory_created:
            shutil.rmtree(local_directory)

        logger.info("End scheduler at %r", scheduler.address)


if __name__ == "__main__":
    main()

