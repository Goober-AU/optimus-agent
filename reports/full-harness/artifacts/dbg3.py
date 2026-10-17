
import sys
print("sys.path[:3]:", sys.path[:3])
import rlm
print("rlm __init__:", rlm.__file__)
from rlm import harness
print("harness file:", harness.__file__)
