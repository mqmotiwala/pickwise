import os
import sys
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# Only configure handlers once.
if not logger.handlers:
    _formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(filename)s - %(message)s"
    )

    # Always log to stdout
    _stdout = logging.StreamHandler(sys.stdout)
    _stdout.setFormatter(_formatter)
    logger.addHandler(_stdout)

    # also log to CloudWatch 
    try:
        import boto3
        import watchtower

        _region = os.getenv("AWS_REGION")
        _key = os.getenv("AWS_ACCESS_KEY_ID")
        _secret = os.getenv("AWS_SECRET_ACCESS_KEY")
        if _region and _key and _secret:
            _logs_client = boto3.client(
                "logs",
                aws_access_key_id=_key,
                aws_secret_access_key=_secret,
                region_name=_region,
            )
            _cw = watchtower.CloudWatchLogHandler(
                log_group_name="/pickwise/ui",
                log_stream_name="{strftime:%Y-%m-%d}",
                boto3_client=_logs_client,
                send_interval=60,
            )
            _cw.setLevel(logging.INFO)
            _cw.setFormatter(_formatter)
            logger.addHandler(_cw)
    except Exception as _cw_err:
        logger.warning("CloudWatch logging disabled, using stdout only: %s", _cw_err)


def log_event(event, user=None, level="info", **fields):
    """
    Emit a consistent, greppable user-activity line for CloudWatch.

    Produces lines like:
        event=user_login user_id=abc123 email=jane@example.com login_count=5
    so CloudWatch Logs Insights can filter and aggregate by event and user_id.

    Args:
        event: short snake_case event name (e.g. "user_login", "trades_synced").
        user: a User instance (user_id/email are read off it) or None.
        level: "debug", "info", "warning", or "error" (default "info").
               Only info and above reach CloudWatch (handler is INFO-level).
        **fields: extra context (ticker, count, error, ...);
                  None values are skipped, values containing spaces are quoted.
    """
    def _fmt(v):
        s = str(v)
        if s == "" or " " in s or "\t" in s or "\"" in s:
            s = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
        return s

    parts = ["event=" + str(event)]
    if user is not None:
        uid = getattr(user, "user_id", None)
        email = getattr(user, "email", None)
        if uid:
            parts.append("user_id=" + _fmt(uid))
        if email:
            parts.append("email=" + _fmt(email))
    for _k, _v in fields.items():
        if _v is not None:
            parts.append(str(_k) + "=" + _fmt(_v))

    _levels = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }
    logger.log(_levels.get(level, logging.INFO), " ".join(parts), stacklevel=2)
