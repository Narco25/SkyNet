#!/usr/bin/env python3

import os

def create_project(project_name):
    if os.path.exists(project_name):
        return f'Error: Project already exists.'
    else:
        os.makedirs(project_name)
        return f'Project {project_name} created successfully.'

def list_projects():
    projects = [name for name in os.listdir() if os.path.isdir(name)]
    return projects

def validate_project_name(project_name):
    if not project_name:
        return False
    if not project_name.islower():
        return False
    if not project_name.replace('-', '').isalnum():
        return False
    return True

# Test cases
if __name__ == '__main__':
    print(validate_project_name('test_project'))  # Should return True
    print(validate_project_name('test project'))  # Should return False
    print(validate_project_name('another_project'))  # Should return True
    print(validate_project_name('another-project'))  # Should return False
    print(create_project('test_project'))  # Should create a directory and return success message
    print(create_project('test_project'))  # Should return error message
    print(create_project('another_project'))  # Should create a directory and return success message
    print(list_projects())  # Should return a list of project names or an empty list if no projects are found