from unittest import TestCase

import smith

from smith import config

class TestConfig(TestCase):
    def test_verify_parboil(self):
        self.assertTrue(config.parboil_root())

    def test_is_host(self):
        config.is_host()


if __name__ == '__main__':
    main()
