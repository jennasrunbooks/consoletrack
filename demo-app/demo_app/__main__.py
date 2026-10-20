"""`python -m demo_app`: the server or the traffic generator, chosen by DEMO_MODE."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from demo_app import config, loadgen, server


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("demo_app")
    try:
        cfg = config.load()
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    gen = None
    if cfg.mode == "loadgen":
        gen, httpd = loadgen.build(cfg)
        threading.Thread(target=gen.run, name="loadgen-scheduler", daemon=True).start()
        log.info(
            "loadgen: %s at %g rps, timeout %gs, report %s",
            cfg.target,
            cfg.rps,
            cfg.timeout_seconds,
            cfg.report or "none",
        )
    else:
        _, httpd = server.build(cfg)
        log.info("serving on :%d, S3 read %s", cfg.port, "on" if cfg.bucket else "off")

    def stop(*_: object) -> None:
        if gen is not None:
            gen.stop()
        # shutdown() waits for serve_forever() to return, so not from the serving thread.
        threading.Thread(target=httpd.shutdown).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with httpd:
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
