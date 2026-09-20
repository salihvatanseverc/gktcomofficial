import logger


def start_runtime():
    try:
        logger.info("Göktürk Python Runtime başlatıldı.")
        return True

    except Exception as error:
        logger.error(f"Runtime başlatma hatası: {error}")
        return False


if __name__ == "__main__":
    start_runtime()