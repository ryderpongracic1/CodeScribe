# plain_importer.py — uses plain import statement
import sample_project.base


def call_base():
    return sample_project.base.base_helper(10)
