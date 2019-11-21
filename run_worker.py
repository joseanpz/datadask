import asyncio
import atexit
import logging
import gc
import os
import signal
import sys
import warnings

import click
import dask
from dask.utils import ignoring
from dask.system import CPU_COUNT
from distributed import Nanny, Worker
from distributed.security import Security
from distributed.cli.utils import check_python_3, install_signal_handlers
from distributed.comm import get_address_host_port
from distributed.preloading import validate_preload_argv
from distributed.proctitle import (
    enable_proctitle_on_children,
    enable_proctitle_on_current,
)
from distributed.utils import deserialize_for_cli

from toolz import valmap
from tornado.ioloop import IOLoop, TimeoutError

logger = logging.getLogger("distributed.dask_worker")

def main(
    scheduler,
    host=None,
    worker_port=0,
    listen_address=None,
    contact_address=None,
    nanny_port=0,
    nthreads=0,
    nprocs=1,
    nanny=True,
    name=None,
    pid_file="",
    resources="",
    dashboard=True,
    bokeh=None,
    bokeh_port=None,
    scheduler_file="",
    dashboard_prefix="",
    tls_ca_file=None,
    tls_cert=None,
    tls_key=None,
    dashboard_address=":0",
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

    sec = Security(
        **{
            k: v
            for k, v in [
                ("tls_ca_file", tls_ca_file),
                ("tls_worker_cert", tls_cert),
                ("tls_worker_key", tls_key),
            ]
            if v is not None
        }
    )

    if nprocs > 1 and worker_port != 0:
        logger.error(
            "Failed to launch worker.  You cannot use the --port argument when nprocs > 1."
        )
        sys.exit(1)

    if nprocs > 1 and not nanny:
        logger.error(
            "Failed to launch worker.  You cannot use the --no-nanny argument when nprocs > 1."
        )
        sys.exit(1)

    if contact_address and not listen_address:
        logger.error(
            "Failed to launch worker. "
            "Must specify --listen-address when --contact-address is given"
        )
        sys.exit(1)

    if nprocs > 1 and listen_address:
        logger.error(
            "Failed to launch worker. "
            "You cannot specify --listen-address when nprocs > 1."
        )
        sys.exit(1)

    if (worker_port or host) and listen_address:
        logger.error(
            "Failed to launch worker. "
            "You cannot specify --listen-address when --worker-port or --host is given."
        )
        sys.exit(1)

    try:
        if listen_address:
            (host, worker_port) = get_address_host_port(listen_address, strict=True)

        if contact_address:
            # we only need this to verify it is getting parsed
            (_, _) = get_address_host_port(contact_address, strict=True)
        else:
            # if contact address is not present we use the listen_address for contact
            contact_address = listen_address
    except ValueError as e:
        logger.error("Failed to launch worker. " + str(e))
        sys.exit(1)

    if nanny:
        port = nanny_port
    else:
        port = worker_port

    if not nthreads:
        nthreads = CPU_COUNT // nprocs

    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

        def del_pid_file():
            if os.path.exists(pid_file):
                os.remove(pid_file)

        atexit.register(del_pid_file)

    if resources:
        resources = resources.replace(",", " ").split()
        resources = dict(pair.split("=") for pair in resources)
        resources = valmap(float, resources)
    else:
        resources = None

    loop = IOLoop.current()

    if nanny:
        kwargs.update({"worker_port": worker_port, "listen_address": listen_address})
        t = Nanny
    else:
        if nanny_port:
            kwargs["service_ports"] = {"nanny": nanny_port}
        t = Worker

    if (
        not scheduler
        and not scheduler_file
        and dask.config.get("scheduler-address", None) is None
    ):
        raise ValueError(
            "Need to provide scheduler address like\n"
            "dask-worker SCHEDULER_ADDRESS:8786"
        )

    with ignoring(TypeError, ValueError):
        name = int(name)

    nannies = [
        t(
            scheduler,
            scheduler_file=scheduler_file,
            nthreads=nthreads,
            loop=loop,
            resources=resources,
            security=sec,
            contact_address=contact_address,
            host=host,
            port=port,
            dashboard_address=dashboard_address if dashboard else None,
            service_kwargs={"dashboard": {"prefix": dashboard_prefix}},
            name=name
            if nprocs == 1 or name is None or name == ""
            else str(name) + "-" + str(i),
            **kwargs
        )
        for i in range(nprocs)
    ]

    async def close_all():
        # Unregister all workers from scheduler
        if nanny:
            await asyncio.gather(*[n.close(timeout=2) for n in nannies])

    signal_fired = False

    def on_signal(signum):
        nonlocal signal_fired
        signal_fired = True
        if signum != signal.SIGINT:
            logger.info("Exiting on signal %d", signum)
        asyncio.ensure_future(close_all())

    async def run():
        await asyncio.gather(*nannies)
        await asyncio.gather(*[n.finished() for n in nannies])

    install_signal_handlers(loop, cleanup=on_signal)

    try:
        loop.run_sync(run)
    except TimeoutError:
        # We already log the exception in nanny / worker. Don't do it again.
        if not signal_fired:
            logger.info("Timed out starting worker")
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("End worker")


if __name__ == "__main__":
    main("tcp://10.24.2.78:8786", name='worker-x')
