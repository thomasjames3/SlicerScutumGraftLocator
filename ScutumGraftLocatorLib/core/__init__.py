"""
core package: the actual image-processing pipeline, independent of any UI.

Every function here takes plain data (numpy arrays, SimpleITK images, lists
of points) in and returns plain data out. None of this code knows it's being
called from 3D Slicer -- that keeps it testable on its own and reusable from
a plain Python script (see main.py) if you ever want to batch-process cases
outside of Slicer.
"""
