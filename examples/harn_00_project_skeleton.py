"""Show that HARN-00 configuration and logging are wired correctly."""

from harness import configure_logging, load_config


def main() -> None:
    config = load_config()
    logger = configure_logging(config)
    logger.info(
        "HARN-00 skeleton is ready (environment=%s, log_level=%s)",
        config.environment.value,
        config.log_level.value,
    )


if __name__ == "__main__":
    main()

