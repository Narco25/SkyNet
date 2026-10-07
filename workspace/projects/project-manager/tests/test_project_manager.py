import unittest
from project_manager import validate_project_name

class TestProjectManager(unittest.TestCase):
    def test_valid_project_names(self):
        self.assertTrue(validate_project_name('project-123'))
        self.assertTrue(validate_project_name('valid-name'))

    def test_invalid_project_names(self):
        self.assertFalse(validate_project_name(''))
        self.assertFalse(validate_project_name(' '))
        self.assertFalse(validate_project_name('InvalidName'))
        self.assertFalse(validate_project_name('project*name'))

if __name__ == '__main__':
    unittest.main()