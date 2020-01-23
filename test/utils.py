import os
import logging

from enum import Enum

class Path(Enum):
    """Helper with paths commonly used during the tests."""

    # TODO: fix
    qiskit_path = ["qiskit/"]

    # Main SDK path:    qiskit/
    SDK = qiskit_path[0]
    # test.python path: qiskit/test/python/
    TEST = os.path.normpath(os.path.join(SDK, '..', 'test'))
    # Examples path:    examples/
    EXAMPLES = os.path.normpath(os.path.join(SDK, '..', 'examples'))
    # Schemas path:     qiskit/schemas
    SCHEMAS = os.path.normpath(os.path.join(SDK, 'schemas'))
    # Sample QASMs path: qiskit/test/python/qasm
    QASMS = os.path.normpath(os.path.join(TEST, 'examples'))

def setup_test_logging(logger, log_level, filename):
    """Set logging to file and stdout for a logger.
    Args:
        logger (Logger): logger object to be updated.
        log_level (str): logging level.
        filename (str): name of the output file.
    """
    # Set up formatter.
    log_fmt = ('{}.%(funcName)s:%(levelname)s:%(asctime)s:'
               ' %(message)s'.format(logger.name))
    formatter = logging.Formatter(log_fmt)

    # Set up the file handler.
    file_handler = logging.FileHandler(filename)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if os.getenv('STREAM_LOG'):
        # Set up the stream handler.
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    # Set the logging level from the environment variable, defaulting
    # to INFO if it is not a valid level.
    level = logging._nameToLevel.get(log_level, logging.INFO)
    logger.setLevel(level)
