import os
import inspect
import logging
import unittest

from .utils import Path, setup_test_logging

class OpenqasmTestCase(unittest.TestCase):
    """Helper class that contains common functionality."""

    @classmethod
    def setUpClass(cls):
        # Determines if the TestCase is using IBMQ credentials.
        cls.using_ibmq_credentials = False

        # Set logging to file and stdout if the LOG_LEVEL envar is set.
        cls.log = logging.getLogger(cls.__name__)
        if os.getenv('LOG_LEVEL'):
            filename = '%s.log' % os.path.splitext(inspect.getfile(cls))[0]
            setup_test_logging(cls.log, os.getenv('LOG_LEVEL'), filename)

    @staticmethod
    def _get_resource_path(filename, path=Path.TEST):
        """Get the absolute path to a resource.
        Args:
            filename (string): filename or relative path to the resource.
            path (Path): path used as relative to the filename.
        Returns:
            str: the absolute path to the resource.
        """
        return os.path.normpath(os.path.join(path.value, filename))

