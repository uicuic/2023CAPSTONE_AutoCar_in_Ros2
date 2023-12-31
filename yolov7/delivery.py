#!/usr/bin/env python3

import time
import cv2
import torch
import statistics
import numpy as np

from numpy import random
from cv_bridge import CvBridge
from models.experimental import attempt_load
from utils.datasets import letterbox
from utils.general import check_img_size, check_requirements, non_max_suppression, scale_coords
from utils.plots import plot_one_box
from utils.torch_utils import select_device, time_synchronized

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import Int32MultiArray, String

WEIGHTS = 'weights/delivery.pt'
IMG_SIZE = 640
DEVICE = ''
AUGMENT = False
CONF_THRES = 0.70
IOU_THRES = 0.45
CLASSES = None
AGNOSTIC_NMS = False

QUEUE_SIZE = 13
CLASS_MAP = (
    ("id_0", "id_1", "id_2"),
    ("A1",),
    ("A2",),
    ("A3",),
    ("B1",),
    ("B2",),
    ("B3",)
    )

# Initialize
device = select_device(DEVICE)
half = device.type != 'cpu'  # half precision only supported on CUDA
print('device:', device)

# Load model
model = attempt_load(WEIGHTS, map_location=device)  # load FP32 model
stride = int(model.stride.max())  # model stride
imgsz = check_img_size(IMG_SIZE, s=stride)  # check img_size
if half:
    model.half()  # to FP16

# Get names and colors
names = model.module.names if hasattr(model, 'module') else model.names
colors = [[random.randint(0, 255) for _ in range(3)] for _ in names]

# Run inference
if device.type != 'cpu':
    model(torch.zeros(1, 3, imgsz, imgsz).to(device).type_as(next(model.parameters())))  # run once

class YOLOv7(Node):
    def __init__(self):

        super().__init__('side')

        self.detected_pub = self.create_publisher(Image, "/yolo/delivery", 10)
        self.delivery_pub = self.create_publisher(Int32MultiArray, "/delivery_sign", 10)

        self.mode_sub = self.create_subscription(String, "/yolo_mode", self.mode_cb, 10)
        self.image_sub = self.create_subscription(Image, "/side/image_raw", self.image_cb, 10)

        self.img_width = IMG_SIZE
        self.mode = 'delivery'
        self.sign = 0

        self.B1 = []
        self.B2 = []
        self.B3 = []

        # [[A queue], [B1 queue], [B2 queue], ...]
        self.queue_list = [[-1 for i in range(QUEUE_SIZE)] for j in range(len(CLASS_MAP))]

        # [[A1 to A queue], [A2 to A queue], [A3 to A queue], [B1 to B1 queue], [B2 to B2 queue], [B3 to B3 queue], ...]
        self.id_to_queue_list = [self.queue_list[i] for i in range(len(CLASS_MAP)) for _ in range(len(CLASS_MAP[i]))]

        self.timer = self.create_timer(0.1, self.yolo_pub)

    def mode_cb(self, msg):
        self.mode = msg.data

    def image_cb(self, img):
        self.img_width = img.width
        check_requirements(exclude=('pycocotools', 'thop'))
        with torch.no_grad():
            bridge = CvBridge()
            cap = bridge.imgmsg_to_cv2(img, desired_encoding="bgr8")

            if self.mode == 'delivery':
                result = self.detect(cap)
                image_message = bridge.cv2_to_imgmsg(result, encoding="bgr8")
            else:
                image_message = bridge.cv2_to_imgmsg(cap, encoding="bgr8")

            image_message.header.stamp = self.get_clock().now().to_msg()
            self.detected_pub.publish(image_message)


    # Detect function
    def detect(self, frame):
        # Load image
        img0 = frame

        # Padded resize
        img = letterbox(img0, imgsz, stride=stride)[0]

        # Convert
        img = img[:, :, ::-1].transpose(2, 0, 1)  # BGR to RGB, to 3x416x416
        img = np.ascontiguousarray(img)

        img = torch.from_numpy(img).to(device)
        img = img.half() if half else img.float()  # uint8 to fp16/32
        img /= 255.0  # 0 - 255 to 0.0 - 1.0
        if img.ndimension() == 3:
            img = img.unsqueeze(0)


        # Inference
        t0 = time_synchronized()
        pred = model(img, augment=AUGMENT)[0]

        # Apply NMS
        pred = non_max_suppression(pred, CONF_THRES, IOU_THRES, classes=CLASSES, agnostic=AGNOSTIC_NMS)

        # Process detections
        det = pred[0]
        numClasses = len(det)

        s = ''
        s += '%gx%g ' % img.shape[2:]  # print string

        if numClasses:
            # Rescale boxes from img_size to img0 size
            det[:, :4] = scale_coords(img.shape[2:], det[:, :4], img0.shape).round()

            # Print results
            for c in det[:, -1].unique():
                n = (det[:, -1] == c).sum()  # detections per class
                s += f"{n} {names[int(c)]}{'s' * (n > 1)}, "  # add to string

            # Write results
            for *xyxy, conf, cls in reversed(det):
                id = int(cls)
                label = f'{names[id]} {conf:.2f}'
                plot_one_box(xyxy, img0, label=label, color=colors[id], line_thickness=3)
                xmin, ymin, xmax, ymax = [int(tensor.item()) for tensor in xyxy]

                xmean = (xmin + xmax) / 2

                if xmean > 50 and xmean < self.img_width - 50:
                    if id in (0, 1, 2):
                        self.id_to_queue_list[id + 3].append(int(xmean))
                        self.id_to_queue_list[0].append(id)
                    
                    if id == 3: 
                        self.B1.append(int(xmean))
                    elif id == 4: 
                        self.B2.append(int(xmean))
                    elif id == 5: 
                        self.B3.append(int(xmean))

                else:
                    for queue in self.queue_list:
                        if len(queue) == QUEUE_SIZE: # append -1 to an undetected classes
                            queue.append(-1)
            
            if len(self.B1):
                bx = min(self.B1)
                self.id_to_queue_list[6].append(bx)
            if len(self.B2):
                bx = min(self.B2)
                self.id_to_queue_list[7].append(bx)
            if len(self.B3):
                bx = min(self.B3)
                self.id_to_queue_list[8].append(bx)
        
            self.B1 = []
            self.B2 = []
            self.B3 = []
        
        else:
            for queue in self.queue_list:
                if len(queue) == QUEUE_SIZE: # append -1 to an undetected classes
                    queue.append(-1)

        # return results
        return img0


    # CLASS ==========================================================================
    # 0 : A1  1 : A2  2 : A3  3 : B1  4 : B2  5: B3
    # ================================================================================

    # OUTPUT =========================================================================
    # [(A.id), (A1.x), (A2.x), (A3.x), (B1.x), (B2.x), (B3.x)]
    # ================================================================================
    def delivery_vote(self, queue):
        if queue.count(-1) > int(QUEUE_SIZE / 2):
            return 0
        else:
            for element in queue: # get latest x_mean
                if element != -1:
                    val = element
            return val


    def hard_vote(self, queue):
        return statistics.mode(queue)


    def yolo_pub(self):
        final_check = Int32MultiArray()

        for queue in self.queue_list:
            while len(queue) != QUEUE_SIZE: # delete first element
                del queue[0]

        queue_list = self.queue_list

        # queue voting
        for idx in range(len(queue_list)):
            if idx == 0: # find A number
                final_check.data.append(self.hard_vote(queue_list[idx]))
            else:
                final_check.data.append(self.delivery_vote(queue_list[idx]))

        if final_check.data[0] != -1:
            self.sign = final_check.data[0] + 1

        if self.sign:
            print(f'Delivery Sign : A{self.sign}, B{self.sign}')
        else:
            print('Not Detected yet')

        self.delivery_pub.publish(final_check)


def main(args=None):
    rclpy.init(args=args)

    try:
        yolo = YOLOv7()

        rclpy.spin(yolo)

    finally:
        yolo.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
