import numpy as np
import pandas as pd
import os
import sys
import datetime
import requests
import json
import importlib
api = importlib.import_module("API_Puller")

os.environ["TF_CPP_MIN_LOG_LEVEL"] = "2"

### Debug logging
# 0 = all messages are logged (default behavior)
# 1 = INFO messages are not printed
# 2 = INFO and WARNING messages are not printed
# 3 = INFO, WARNING, and ERROR messages are not printed

import tensorflow as tf
from tensorflow import keras
from tensorflow.keras.layers import Dense, LSTM, Dropout
from tensorflow.keras import Sequential
from tensorflow.keras.callbacks import EarlyStopping, TensorBoard
from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import MinMaxScaler
from tensorflow.python import debug as tf_debug
from tensorboard.plugins.hparams import api as hp

model_version = 10

print("\nVisible Devices:", tf.config.get_visible_devices())

tf.config.experimental_run_functions_eagerly(True)

_batch_size = 1
_buffer_size = 10000

_max_epochs = 100
_back_in_time = 60 # Days
_step = 1 # Days to offset next dataset
_target_size = 1 # How many to predict



### Hyperparamters
hp_hidden_num_layers = hp.HParam('hidden_num_layers', hp.IntInterval(0, 4))
hp_optimizer = hp.HParam('optimizer', hp.Discrete(['nadam', 'adam', 'rmsprop', 'sgd']))
hp_output_units = hp.HParam('output_units', hp.Discrete([50, 300, 600]))

hp.hparams_config(
    hparams=[hp_hidden_num_layers, hp_optimizer, hp_output_units],
    metrics=[hp.Metric('mae', display_name="Mean Absolute Error")]
)



### Optimizers
_optimizer = keras.optimizers.Nadam()
# keras.optimizers.RMSprop()
# keras.optimizers.Nadam()
# keras.optimizers.Adam()

### Losses
_loss = keras.losses.mean_absolute_error
# keras.losses.mean_squared_error
# keras.losses.mean_absolute_error



train = api.pulldata2()

time_now_string = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
log_dir = "logs/"
models_dir = "models/"

### Handle Data ###
def handle_data(dataset, target, start_index, end_index, history_size, target_size, step, single_step=False):
    
    data = []
    labels = []

    for i in range(0, len(dataset) - history_size, step):
        seq = dataset[i:i + history_size]
        label = target[i + history_size - 1]
        data.append(seq)
        labels.append(label)

    return data, labels

scaler = MinMaxScaler(feature_range=(0,1))
rescaledX = scaler.fit_transform(train[:,2:-1])

rescaledX = np.hstack((rescaledX, train[:,1:2]))

print("Making timestep sets (Step size: %s, History: %s days, Target value size: %s day(s))" % (_step, _back_in_time, _target_size))

X, y = handle_data(
    rescaledX, train[:, -1], 
    0, 
    len(train), 
    _back_in_time, 
    _target_size,
    _step, 
    single_step=True)

train_csv = pd.DataFrame(rescaledX, columns=[
    "maintenance_day",
    "produced_today",
    "times_down_today",
    "amount_down_today",
    "day_of_week"
 ])

train_csv['days_to_maintenance'] = train[:,-1]
train_csv.to_csv("/models/train.csv", index=False)

X_train, X_tmp, y_train, y_tmp = train_test_split(X, y, test_size=0.20)
X_val, X_test, y_val, y_test = train_test_split(X_tmp, y_tmp, test_size=0.50)

print("Made", len(y), "datasets total...")
print("Made", len(y_train), "train datasets...")
print("Made", len(y_val), "validation datasets...")
print("Made", len(y_test), "test datasets...")

train_dataset = tf.data.Dataset.from_tensor_slices((X_train, y_train))
train_dataset = train_dataset.cache().shuffle(_buffer_size).batch(_batch_size).repeat()

val_dataset = tf.data.Dataset.from_tensor_slices((X_val, y_val))
val_dataset = val_dataset.cache().shuffle(_buffer_size).batch(_batch_size).repeat()

test_dataset = tf.data.Dataset.from_tensor_slices(X_test)
test_dataset = test_dataset.cache().batch(_batch_size)



### Models
models = []

model_std = Sequential([
    LSTM(50, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(50),
    Dense(1)
], name="model_std")
# models.append(model_std)


model_wide = Sequential([
    LSTM(300, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(300),
    Dense(1)
], name="model_wide")
# models.append(model_wide)

model_mega_wide = Sequential([
    LSTM(600, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(600),
    Dense(1)
], name="model_mega_wide")
# models.append(model_mega_wide)

model_deep = Sequential([
    LSTM(50, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(50, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(50, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(50, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(50),
    Dense(1)
], name="model_deep")
# models.append(model_deep)

model_shallow_deep = Sequential([
    LSTM(50, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(50, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(50),
    Dense(1) 
    
], name="model_shallow_deep")
models.append(model_shallow_deep)

model_wide_deep = Sequential([
    LSTM(300, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(300, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(300, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(300, input_shape=X_train[0].shape, return_sequences=True),
    LSTM(300),
    Dense(1)
], name="model_wide_deep")
# models.append(model_wide_deep)



### Define callbacks
def get_callbacks(name, hparams):
    log_dir_path = log_dir + str(model_version) + "/" + name
    return [
        EarlyStopping(monitor="val_loss", patience=10, min_delta=0.01),
        TensorBoard(
            log_dir=log_dir_path,
            histogram_freq=1,
            embeddings_freq=1,
            profile_batch=4
        ),
        hp.KerasCallback(log_dir_path, hparams, name)
    ]



### Compile and Fit
def compile_and_fit(model, name, hparams, optimizer=_optimizer, loss=_loss, max_epochs=_max_epochs):
    model.compile(loss=loss, optimizer=optimizer)

    model.summary()
    print("Optimizer:", model.optimizer)

    print("\nTraining model...")

    model_history = model.fit(
        train_dataset, 
        epochs=max_epochs, 
        steps_per_epoch=len(y_train), 
        validation_data=val_dataset, 
        validation_steps=len(y_val), 
        verbose=1, 
        callbacks=get_callbacks(name, hparams))
    
    return model_history

print("\n\n")



def model_builder(name, hparams):

    model = Sequential(name=name)

    if hparams[hp_hidden_num_layers] == 0:
        model.add(LSTM(hparams[hp_output_units], input_shape=X_train[0].shape))
    else:
        model.add(LSTM(hparams[hp_output_units], input_shape=X_train[0].shape, return_sequences=True))

    for i in range(hparams[hp_hidden_num_layers]):
        if i == (hparams[hp_hidden_num_layers] - 1):
            model.add(LSTM(hparams[hp_output_units]))
        else:
            model.add(LSTM(hparams[hp_output_units], input_shape=X_train[0].shape, return_sequences=True))

    model.add(Dense(1))

    return model

session_version = 0

### Trainer loop
for output_units in hp_output_units.domain.values:
    for hidden_num_layers in (hp_hidden_num_layers.domain.min_value, hp_hidden_num_layers.domain.max_value):
        for optimizer in hp_optimizer.domain.values:
            hparams = {
                hp_hidden_num_layers: hidden_num_layers,
                hp_optimizer: optimizer,
                hp_output_units: output_units
            }

            print("Starting session:", session_version)
            print({h.name: hparams[h] for h in hparams})

            model_tmp = model_builder(str(session_version), hparams)
            compile_and_fit(model_tmp, model_tmp.name, hparams, hparams[hp_optimizer])

            session_version += 1



# print("\n\n\nBeginning predictions...")

# predictions = model_shallow_deep.predict(test_dataset, verbose=1)
# predictions_count = len(predictions)
# print("Predicions:", predictions_count)

# total_difference = 0
# total_difference_t = 0
# for i in range(predictions_count):

#     # Used round and int becouse without int you get some '-0.0' numbers.
#     prediction_t = predictions[i][0]
#     prediction = int(round(prediction_t))

#     actual_t = y_test[i]
#     actual = int(actual_t)

#     difference_t = np.sqrt(np.power((actual_t - prediction_t), 2))
#     difference = np.sqrt(np.power((actual - prediction), 2))

#     total_difference_t += difference_t
    
#     if difference == 0:
#         status = "[ ]"
#     else:
#         total_difference += difference
#         status = "[x]"
#     print("Predicted %s day(s), Actual %s day(s), Difference %s day(s) - %s" % (prediction, actual, difference, status))

# print("\nReal world Mean Absolute Error: %s day(s)" % (total_difference / predictions_count))
# print("Mean Absolute Error: %s day(s)" % (total_difference_t / predictions_count))



# NOTE: No space on docker volumes = "Fail to find the dnn implementation."

# New Links
# https://www.tensorflow.org/api_docs/python/tf/keras/models/load_model?version=nightly
# https://www.tensorflow.org/api_docs/python/tf/keras/models/save_model?version=nightly
# https://www.tensorflow.org/guide/keras/save_and_serialize
# https://tutorialdeep.com/knowhow/round-float-to-2-decimal-places-python/
# Getting started with Tensorflow in Google Colaboratory https://www.youtube.com/watch?v=PitcORQSjNM
# Get started with Google Colab https://www.youtube.com/watch?v=inN8seMm7UI
# https://jupyter.org/install
# https://research.google.com/colaboratory/local-runtimes.html
# https://www.analyticsvidhya.com/blog/2016/08/evolution-core-concepts-deep-learning-neural-networks/

# Links
# https://www.tensorflow.org/api_docs/python/tf/keras/Model?version=nightly#fit
# https://www.tensorflow.org/api_docs/python/tf/keras/callbacks/TensorBoard?version=nightly
# https://www.tensorflow.org/install
# https://www.tensorflow.org/api_docs/python/tf/keras/callbacks/EarlyStopping?version=nightly
# https://tensorboard.dev/#get-started
# https://github.com/pytorch/pytorch/issues/22676
# https://www.tensorflow.org/tutorials/keras/overfit_and_underfit#training_procedure
# https://github.com/Createdd/Writing/blob/master/2018/articles/DebugTFBasics.md#2-use-the-tfprint-operation
# https://github.com/haribaskar/Keras_Cheat_Sheet_Python
# https://stackoverflow.com/questions/3002085/python-to-print-out-status-bar-and-percentage
# https://www.tensorflow.org/api_docs/python/tf/executing_eagerly
# https://docs.docker.com/storage/volumes/
# https://www.tensorflow.org/api_docs/python/tf/keras/losses/MeanSquaredError?version=nightly
# https://www.tensorflow.org/api_docs/python/tf/keras/Model?version=nightly#fit
# https://stackoverflow.com/questions/31448821/how-to-write-data-to-host-file-system-from-docker-container
# https://github.com/tensorflow/tensorflow/issues/7652
# https://stackoverflow.com/questions/35911252/disable-tensorflow-debugging-information
# https://stackoverflow.com/questions/509211/understanding-slice-notation
# https://www.analyticsvidhya.com/blog/2018/10/predicting-stock-price-machine-learningnd-deep-learning-techniques-python/
# https://www.tensorflow.org/tutorials/keras/overfit_and_underfit#training_procedure
# https://github.com/Createdd/Writing/blob/master/2018/articles/DebugTFBasics.md#2-use-the-tfprint-operation
# https://github.com/haribaskar/Keras_Cheat_Sheet_Python
# https://stackoverflow.com/questions/3002085/python-to-print-out-status-bar-and-percentage
# https://www.tensorflow.org/api_docs/python/tf/executing_eagerly
# https://docs.docker.com/storage/volumes/
# https://www.tensorflow.org/api_docs/python/tf/keras/losses/MeanSquaredError?version=nightly
# https://www.tensorflow.org/api_docs/python/tf/keras/Model?version=nightly#fit
# https://stackoverflow.com/questions/31448821/how-to-write-data-to-host-file-system-from-docker-container
# https://github.com/tensorflow/tensorflow/issues/7652
# https://stackoverflow.com/questions/35911252/disable-tensorflow-debugging-information
# https://stackoverflow.com/questions/509211/understanding-slice-notation
# https://www.analyticsvidhya.com/blog/2018/10/predicting-stock-price-machine-learningnd-deep-learning-techniques-python/
# https://www.analyticsvidhya.com/blog/2017/12/fundamentals-of-deep-learning-introduction-to-lstm/