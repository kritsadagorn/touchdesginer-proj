import pyrealsense2 as rs
import numpy as np

pipeline = rs.pipeline()
config = rs.config()
config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
pipeline.start(config)

frames = pipeline.wait_for_frames()
depth = frames.get_depth_frame()
data = np.asanyarray(depth.get_data()).astype(np.float32) / 1000.0

print(f"Min depth: {data[data>0].min():.2f}m")
print(f"Max depth: {data.max():.2f}m")
print(f"Mean depth: {data[data>0].mean():.2f}m")
pipeline.stop()