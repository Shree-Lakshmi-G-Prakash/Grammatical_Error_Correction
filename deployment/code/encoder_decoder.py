# -*- coding: utf-8 -*-
"""Encoder_Decoder.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1lkwPsjlj87c9cHXqWVBOQbnYO-po8cHm
"""

#import matplotlib.pyplot as plt
#import matplotlib.ticker as ticker
#from sklearn.model_selection import train_test_split
#import nltk.translate.gleu_score as gleu

import unicodedata
import re
import numpy as np
import os
import io
import time
import pickle
import random
import pandas as pd
from tqdm import tqdm

import tensorflow
from tensorflow.keras.preprocessing.text import Tokenizer
from tensorflow.keras.preprocessing.sequence import  pad_sequences
from tensorflow.keras.layers import Embedding, LSTM, Dense, Dropout, LSTMCell
from tensorflow.keras.callbacks import EarlyStopping, ModelCheckpoint, TensorBoard, ReduceLROnPlateau, TensorBoard

import tensorflow_addons as tfa

class Encoder(tensorflow.keras.Model):

  def __init__(self, vocab_size, output_dim, input_length, lstm_units, batch_size, embedding_matrix=None, em_trainable=False):
    super().__init__()
    self.lstm_units = lstm_units
    self.batch_size = batch_size
    self.en_hidden_state = 0
    self.en_cell_state = 0
    self.en_output = 0

    #define layers
    if embedding_matrix is None:
      self.embedding = Embedding(input_dim=vocab_size, output_dim=output_dim, input_length=input_length, mask_zero=True, name="encoder_embedding")
    else:
      self.embedding = Embedding(input_dim=vocab_size, output_dim=output_dim, input_length=input_length, mask_zero=True,\
                                 weights=[embedding_matrix], trainable=em_trainable, name='encoder_embedding')
      
    self.encoder = LSTM(units=self.lstm_units,return_state=True, return_sequences=True, name='encoder')

  def call(self, input_sequences, states):

    em_output = self.embedding(input_sequences)
    self.en_output, self.en_hidden_state, self.en_cell_state = self.encoder(em_output, initial_state=states)

    return self.en_output, self.en_hidden_state, self.en_cell_state

  def initialize_states(self):

    self.en_hidden_state = tensorflow.zeros(shape=(self.batch_size, self.lstm_units))
    self.en_cell_state = tensorflow.zeros(shape=(self.batch_size, self.lstm_units))

    return [self.en_hidden_state, self.en_cell_state]

class Decoder(tensorflow.keras.Model):
  def __init__(self, vocab_size, output_dim, input_length, dec_units, batch_sz, embedding_matrix=None, em_trainable=False, attention_type='luong'):
    super().__init__()
    self.batch_sz = batch_sz
    self.dec_units = dec_units
    self.attention_type = attention_type
    self.input_length = input_length

    # Define Embedding Layer
    if embedding_matrix is None:
      self.embedding = Embedding(input_dim=vocab_size, output_dim=output_dim, input_length=input_length, name='decoder_embedding')
    else:
      self.embedding = Embedding(input_dim=vocab_size, output_dim=output_dim, input_length=input_length, \
                                 weights=[embedding_matrix], trainable=em_trainable)

    #Final Dense layer on which softmax will be applied
    self.fc = Dense(vocab_size+1)

    # Define the fundamental cell for decoder recurrent structure
    self.decoder_rnn_cell = LSTMCell(self.dec_units)

    # Sampler
    self.sampler = tfa.seq2seq.sampler.TrainingSampler()


    # Create attention mechanism with memory = None
    self.attention_mechanism = self.build_attention_mechanism(self.dec_units, 
                                                              None, self.batch_sz*[self.input_length], self.attention_type)

    # Wrap attention mechanism with the fundamental rnn cell of decoder
    self.rnn_cell = self.build_rnn_cell(batch_sz)

    # Define the decoder with respect to fundamental rnn cell
    self.decoder = tfa.seq2seq.BasicDecoder(self.rnn_cell, sampler=self.sampler, output_layer=self.fc)


  def build_rnn_cell(self, batch_sz):
    rnn_cell = tfa.seq2seq.AttentionWrapper(self.decoder_rnn_cell, 
                                  self.attention_mechanism, attention_layer_size=self.dec_units)
    return rnn_cell

  def build_attention_mechanism(self, dec_units, memory, memory_sequence_length, attention_type='luong'):
    # ------------- #
    # typ: Which sort of attention (Bahdanau, Luong)
    # dec_units: final dimension of attention outputs 
    # memory: encoder hidden states of shape (batch_size, max_length_input, enc_units)
    # memory_sequence_length: 1d array of shape (batch_size) with every element set to max_length_input (for masking purpose)

    if(attention_type=='bahdanau'):
      return tfa.seq2seq.BahdanauAttention(units=dec_units, memory=memory, memory_sequence_length=memory_sequence_length)
    else:
      return tfa.seq2seq.LuongAttention(units=dec_units, memory=memory, memory_sequence_length=memory_sequence_length)

  def build_initial_state(self, batch_sz, encoder_state, Dtype):
    decoder_initial_state = self.rnn_cell.get_initial_state(batch_size=batch_sz, dtype=Dtype)
    decoder_initial_state = decoder_initial_state.clone(cell_state=encoder_state)
    return decoder_initial_state


  def call(self, inputs, initial_state):
    x = self.embedding(inputs)
    outputs, _, _ = self.decoder(x, initial_state=initial_state, sequence_length=self.batch_sz*[self.input_length])
    return outputs

class Encoder_Decoder_Attention(tensorflow.keras.Model):

  def __init__(self, en_vocab_size, en_out_dim, en_input_length, en_lstm, de_vocab_size, de_out_dim, de_input_length, de_lstm, batch_size,\
               attention='loung', en_embeddings=None, en_emb_trainable=False, de_embeddings=None, de_emb_trainable=False):

    super().__init__()
    #obj to encoder
    self.batch_size = batch_size
    self.encoder = Encoder(en_vocab_size, en_out_dim, en_input_length, en_lstm, batch_size, en_embeddings, en_emb_trainable)
    self.decoder = Decoder(de_vocab_size, de_out_dim, de_input_length, de_lstm, batch_size, de_embeddings, de_emb_trainable ,attention)


  def call(self, data):

    input_seq, target_seq = data[0], data[1]

    #get encoder initial states
    enc_states = self.encoder.initialize_states()

    #encod input
    enc_output, en_hidden_state, en_cell_state = self.encoder(input_seq, enc_states)

    #set attention with encoder output
    self.decoder.attention_mechanism.setup_memory(enc_output)

    #get initial ststes for decoder
    de_initial_state = self.decoder.build_initial_state(self.batch_size, [en_hidden_state, en_cell_state], tensorflow.float32)

    #get predictions from decoder
    pred = self.decoder(target_seq, de_initial_state)

    #return the final output
    return pred.rnn_output