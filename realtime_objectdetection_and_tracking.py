# USAGE
# python realtime_objectdetection_and_tracking.py --input videos/koper_highway.mp4 --display 1 --output koper_highway.avi --mask 200,350,650,550 --resize 1024

# import the necessary packages
import os
from imutils.video import VideoStream, WebcamVideoStream
from imutils.video import FPS
import argparse
import time
import cv2
import numpy as np
from pyimagesearch.centroidtracker import CentroidTracker
from pyimagesearch.trackableobject import TrackableObject
import dlib

running_on_rpi = False


#os_info = os.uname()
#if os_info[4][:3] == 'arm':
#    running_on_rpi = True

# check if optimization is enabled
if not cv2.useOptimized():
    print("By default, OpenCV has not been optimized")
    cv2.setUseOptimized(True)


writer = None
W = None
H = None

observation_mask = None
display_bounding_boxes = False

display_settings = True

# initialize the total number of frames processed thus far, along
# with the total number of objects that have moved either up or down
totalFrames = 0
totalOverall = 0

image_for_result = None


# initialize the list of class labels our network was trained to
# detect, then generate a set of bounding box colors for each class
CLASSES = ("background", "aeroplane", "bicycle", "bird",
           "boat", "bottle", "bus", "car", "cat", "chair", "cow",
           "diningtable", "dog", "horse", "motorbike", "person",
           "pottedplant", "sheep", "sofa", "train", "tvmonitor")


def predict(frame, net):

    # Prepare input blob and perform an inference
    #blob = cv2.dnn.blobFromImage(frame, 0.007843, size=(300, 300), mean=(127.5, 127.5, 127.5), swapRB=False, crop=False)
    blob = cv2.dnn.blobFromImage(frame, 0.007843, size=(416, 416), mean=(127.5, 127.5, 127.5), swapRB=False, crop=False)
    net.setInput(blob)
    out = net.forward()
    out = out.flatten()

    predictions = []

    for box_index in range(100):
        if out[box_index + 1] == 0.0:
            break
        base_index = box_index * 7
        if (not np.isfinite(out[base_index]) or
                not np.isfinite(out[base_index + 1]) or
                not np.isfinite(out[base_index + 2]) or
                not np.isfinite(out[base_index + 3]) or
                not np.isfinite(out[base_index + 4]) or
                not np.isfinite(out[base_index + 5]) or
                not np.isfinite(out[base_index + 6])):
            continue


        object_info_overlay = out[base_index:base_index + 7]

        base_index = 0
        class_id = int(object_info_overlay[base_index + 1])
        conf = object_info_overlay[base_index + 2]
        if (conf <= args["confidence"] or class_id != 7):
            continue

        box_left = object_info_overlay[base_index + 3]
        box_top = object_info_overlay[base_index + 4]
        box_right = object_info_overlay[base_index + 5]
        box_bottom = object_info_overlay[base_index + 6]

        prediction_to_append = [class_id, conf, ((box_left, box_top), (box_right, box_bottom))]
        predictions.append(prediction_to_append)

    return predictions


def resize(frame, width, height=None):
    h, w, _ = frame.shape
    if height is None:
        # keep ratio
        factor = width * 1.0 / w
        height = int(factor * h)
    frame_resized = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
    return frame_resized


def crop(frame, top, left, height, width):
    h, w, _ = frame.shape
    cropped = frame[top:top + height, left: left + width]
    return cropped


def draw_observation_mask(frame, top_left, bottom_right, alpha=0.5, color=(0, 0, 255)):
    # create two copies of the original image -- one for
    # the overlay and one for the final output image
    overlay = frame.copy()
    output = frame.copy()

    # draw a red rectangle surrounding Adrian in the image
    # along with the text "PyImageSearch" at the top-left
    # corner
    cv2.rectangle(overlay, top_left, bottom_right,
                  color, -1)
    # apply the overlay
    cv2.addWeighted(overlay, alpha, output, 1 - alpha,
                    0, output)
    return output


def adjust_gamma(image, gamma=1.0):
    # build a lookup table mapping the pixel values [0, 255] to
    # their adjusted gamma values
    invGamma = 1.0 / gamma
    table = np.array([((i / 255.0) ** invGamma) * 255
        for i in np.arange(0, 256)]).astype("uint8")
 
    # apply gamma correction using the lookup table
    return cv2.LUT(image, table)



# construct the argument parser and parse the arguments
ap = argparse.ArgumentParser()
ap.add_argument("-c", "--confidence", default=.5,
                help="confidence threshold")
ap.add_argument("-d", "--display", type=int, default=0,
                help="switch to display image on screen")
ap.add_argument("-i", "--input", type=str,
                help="path to optional input video file")
ap.add_argument("-o", "--output", type=str,
                help="path to optional output video file")
ap.add_argument("-s", "--skip-frames", type=int, default=10,
	help="# of skip frames between detections")
ap.add_argument("-r", "--resize", type=str, default=None,
                help="resized frames dimensions, e.g. 320,240")
ap.add_argument("-m", "--mask", type=str, default=None,
                help="observation mask x_min,y_min,x_max,y_max, e.g. 50,70,220,300")
args = vars(ap.parse_args())

if args["mask"] is not None:
    try:
        x_min, y_min, x_max, y_max = [int(item.replace(" ", "")) for item in args["mask"].split(",")]
        observation_mask = [(x_min, y_min), (x_max, y_max)]
    except ValueError:
        print("Invalid mask format!")

# instantiate our centroid tracker, then initialize a list to store
# each of our dlib correlation trackers, followed by a dictionary to
# map each unique object ID to a TrackableObject
centroidTracker_max_disappeared = 15
centroidTracker_max_distance = 100
ct = CentroidTracker(maxDisappeared=centroidTracker_max_disappeared, maxDistance=centroidTracker_max_distance, mask=observation_mask)
trackers = []
trackableObjects = {}

# Load the model
#net = cv2.dnn.readNet('models/mobilenet-ssd/FP16/mobilenet-ssd.xml', 'models/mobilenet-ssd/FP16/mobilenet-ssd.bin')
net = cv2.dnn.readNetFromCaffe("models/MobileNetSSD_deploy.prototxt", "models/MobileNetSSD_deploy.caffemodel")

# Specify target device
#net.setPreferableTarget(cv2.dnn.DNN_TARGET_MYRIAD)

# if a video path was not supplied, grab a reference to the webcam
if not args.get("input", False):
    print("[INFO] starting video stream...")
    #vs = VideoStream(src=0).start()
    vs = WebcamVideoStream(src=0).start()
    time.sleep(2.0)

# otherwise, grab a reference to the video file
else:
    print("[INFO] opening video file...")
    vs = cv2.VideoCapture(args["input"])

time.sleep(1)
fps = FPS().start()

# loop over frames from the video file stream
while True:
    try:
        # grab the frame from the threaded video stream
        # make a copy of the frame and resize it for display/video purposes
        frame = vs.read()
        frame = frame[1] if args.get("input", False) else frame

        # if we are viewing a video and we did not grab a frame then we
        # have reached the end of the video
        if args["input"] is not None and frame is None:
            break

        if args["resize"] is not None:
            if "," in args["resize"]:
                w, h = [int(item) for item in args["resize"].split(",")]
                frame = resize(frame, width=w, height=h)
            else:
                frame = resize(frame, width=int(args["resize"]))

        frame = adjust_gamma(frame, gamma=1.5)

        # the frame from BGR to RGB for dlib
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        H, W, _ = frame.shape

        # print settings
        if display_settings:
            print("[INFO] frame size (W x H): %d x %d" % (W, H))
            preview_image = frame.copy()
            preview_image_file = "screenshots/preview_%d_%d" % (W, H)
            if observation_mask is not None:
                print("Observation mask (top left, bottom right): %s" % str(observation_mask))
                preview_image = draw_observation_mask(preview_image, observation_mask[0], observation_mask[1])
                preview_image_file += "_mask_%d_%d_%d_%d" % (observation_mask[0][0], observation_mask[0][1], observation_mask[1][0], observation_mask[1][1])
            preview_image_file += ".jpg"
            cv2.imwrite(preview_image_file, preview_image)
            display_settings = False

        if args["display"] > 0 or args["output"] is not None:
            image_for_result = frame.copy()
            if observation_mask is not None:
                image_for_result = draw_observation_mask(image_for_result, observation_mask[0], observation_mask[1])

        # if we are supposed to be writing a video to disk, initialize
        # the writer
        if args["output"] is not None and writer is None:
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            writer = cv2.VideoWriter(args["output"], fourcc, 30,
                                     (frame.shape[1], frame.shape[0]), True)

        # initialize the current status along with our list of bounding
        # box rectangles returned by either (1) our object detector or
        # (2) the correlation trackers
        status = "Waiting"
        rects = []

        # check to see if we should run a more computationally expensive
        # object detection method to aid our tracker
        if totalFrames % args["skip_frames"] == 0:
            # set the status and initialize our new set of object trackers
            status = "Detecting"
            trackers = []


            # use the NCS to acquire predictions
            if observation_mask is not None:
                cropped_frame = frame[observation_mask[0][1]:observation_mask[1][1], observation_mask[0][0]:observation_mask[1][0]]
                predictions = predict(cropped_frame, net)
            else:
                predictions = predict(frame, net)

            # loop over our predictions
            for (i, pred) in enumerate(predictions):
                # extract prediction data for readability
                (class_id, pred_conf, pred_boxpts) = pred
                ((x_min, y_min), (x_max, y_max)) = pred_boxpts

                # filter out weak detections by ensuring the `confidence`
                # is greater than the minimum confidence
                if pred_conf > args["confidence"]:
                    # print prediction to terminal
                    print("[INFO] Prediction #{}: confidence={}, "
                          "boxpoints={}".format(i, pred_conf,
                                                pred_boxpts))


                    # if the class label is not a car, ignore it
                    if CLASSES[class_id] not in ["car", "person"]:
                        continue

                    if observation_mask is not None:
                        mask_width = observation_mask[1][0] - observation_mask[0][0]
                        mask_height = observation_mask[1][1] - observation_mask[0][1]
                        x_min = int(x_min * mask_width) + observation_mask[0][0]
                        y_min = int(y_min * mask_height) + observation_mask[0][1]
                        x_max = int(x_max * mask_width) + observation_mask[0][0]
                        y_max = int(y_max * mask_height) + observation_mask[0][1]
                    else:
                        x_min = int(x_min * W)
                        y_min = int(y_min * H)
                        x_max = int(x_max * W)
                        y_max = int(y_max * H)

                    # construct a dlib rectangle object from the bounding
                    # box coordinates and then start the dlib correlation
                    # tracker
                    tracker = dlib.correlation_tracker()
                    rect = dlib.rectangle(x_min, y_min, x_max, y_max)
                    tracker.start_track(rgb, rect)

                    # add the tracker to our list of trackers so we can
                    # utilize it during skip frames
                    trackers.append(tracker)

        # otherwise, we should utilize our object *trackers* rather than
        # object *detectors* to obtain a higher frame processing throughput
        else:
            # loop over the trackers
            for tracker in trackers:
                # set the status of our system to be 'tracking' rather
                # than 'waiting' or 'detecting'
                status = "Tracking"

                # update the tracker and grab the updated position
                tracker.update(rgb)
                pos = tracker.get_position()

                # unpack the position object
                startX = int(pos.left())
                startY = int(pos.top())
                endX = int(pos.right())
                endY = int(pos.bottom())

                # add the bounding box coordinates to the rectangles list
                rects.append((startX, startY, endX, endY))

        # use the centroid tracker to associate the (1) old object
        # centroids with (2) the newly computed object centroids
        objects = ct.update(rects)

        # loop over the tracked objects
        for (objectID, centroid) in objects.items():
            # check to see if a trackable object exists for the current
            # object ID
            to = trackableObjects.get(objectID, None)

            # if there is no existing trackable object, create one
            if to is None:
                to = TrackableObject(objectID, centroid)

            # otherwise, there is a trackable object so we can utilize it
            # to determine direction
            else:
                y = [c[1] for c in to.centroids]
                to.centroids.append(centroid)

                # check to see if the object has been counted or not
                if not to.counted:
                    totalOverall += 1
                    to.counted = True


            # store the trackable object in our dictionary
            trackableObjects[objectID] = to

            # build a label
            label = "{}: {:.2f}%".format(CLASSES[class_id], pred_conf * 100)

            # extract information from the prediction boxpoints
            y = y_min - 15 if y_min - 15 > 15 else y_min + 15

            if image_for_result is not None:
                if display_bounding_boxes:
                    # display the rectangle and label text
                    cv2.rectangle(image_for_result, (x_min, y_min), (x_max, y_max),
                                  (255, 0, 0), 2)
                    cv2.putText(image_for_result, label, (x_min, y),
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)

                # draw both the ID of the object and the centroid of the
                # object on the output frame
                text = "ID {}".format(objectID)
                cv2.putText(image_for_result, text, (centroid[0] - 10, centroid[1] - 10),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                cv2.circle(image_for_result, (centroid[0], centroid[1]), 4, (0, 255, 0), -1)

        # construct a tuple of information we will be displaying on the
        # frame
        info = [
            ("Count", totalOverall),
            ("Status", status),
        ]

        if image_for_result is not None:
            # loop over the info tuples and draw them on our frame
            for (i, (k, v)) in enumerate(info):
                text = "{}: {}".format(k, v)
                cv2.putText(image_for_result, text, (10, H - ((i * 20) + 20)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

        # check to see if we should write the frame to disk
        if writer is not None:
            writer.write(image_for_result)

            # check if we should display the frame on the screen
        # with prediction data (you can achieve faster FPS if you
        # do not output to the screen)
        if args["display"] > 0:
            # display the frame to the screen
            cv2.imshow("Output", image_for_result)
            key = cv2.waitKey(1) & 0xFF

            # if the `q` key was pressed, break from the loop
            if key == ord("q"):
                break

        # increment the total number of frames processed thus far and
        # then update the FPS counter
        totalFrames += 1
        fps.update()

    # if "ctrl+c" is pressed in the terminal, break from the loop
    except KeyboardInterrupt:
        break

    # if there's a problem reading a frame, break gracefully
    except AttributeError:
        break

# stop the FPS counter timer
fps.stop()

# destroy all windows if we are displaying them
if args["display"] > 0:
    cv2.destroyAllWindows()

# if we are not using a video file, stop the camera video stream
if not args.get("input", False):
    vs.stop()

# otherwise, release the video file pointer
else:
    vs.release()

# display FPS information
print("[INFO] elapsed time: {:.2f}".format(fps.elapsed()))
print("[INFO] approx. FPS: {:.2f}".format(fps.fps()))

