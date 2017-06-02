import tensorflow as tf
import numpy as np
import util
from util import word_vocab
# from tensorflow.python.ops import variable_scope
from tensorflow.contrib.tensorboard.plugins import projector
import time
from sys import stdout
import os
import glob
import operator
import csv
import pandas as pd
import pickle as pkl
from collections import defaultdict


class Skipthought_para(object):

    def __init__(self, embedding_size, hidden_size, hidden_layers, batch_size, keep_prob_dropout, learning_rate, bidirectional, loss_function, sampled_words, num_epochs):
        self.embedding_size = embedding_size
        self.hidden_size = hidden_size
        self.hidden_layers = hidden_layers
        self.batch_size = batch_size
        self.keep_prob_dropout = keep_prob_dropout
        self.learning_rate = learning_rate
        self.bidirectional = bidirectional
        self.loss_function = loss_function
        self.sampled_words = sampled_words
        self.num_epochs = num_epochs

class Skipthought_model(object):

    def __init__(self, data, vocab, parameters, path):
        self.data = data
        self.para = parameters
        self.vocab = vocab
        self.reverse_vocab = dict(zip(self.vocab.values(), self.vocab.keys()))
        self.sorted_vocab = sorted(self.vocab.items(), key=operator.itemgetter(1))
        self.vocabulary_size = len(self.sorted_vocab)
        self.path = path
        self.corpus_length = len(self.data.enc_data)
        self.corpus_stats()
        
        print('\r~~~~~~~ Building graph ~~~~~~~\r')
        self.graph = tf.get_default_graph()
        self.initializer = tf.random_normal_initializer()

        # Variables
        self.word_embeddings = tf.get_variable('embeddings', [self.vocabulary_size, self.para.embedding_size], tf.float32, initializer = self.initializer)
        self.W_pre = tf.get_variable('precoder/weight', [self.para.embedding_size, self.vocabulary_size], tf.float32, initializer = self.initializer)
        self.b_pre = tf.get_variable('precoder/bias', [self.vocabulary_size], tf.float32, initializer = self.initializer)
        self.W_post = tf.get_variable('postcoder/weight', [self.para.embedding_size, self.vocabulary_size], tf.float32, initializer = self.initializer)
        self.b_post = tf.get_variable('postcoder/bias', [self.vocabulary_size], tf.float32, initializer = self.initializer)
        self.global_step = tf.Variable(0, name = 'global_step', trainable = False)

        # Encoder placeholders
        self.sentences = tf.placeholder(tf.int32, [None, None], "sentences")
        self.sentences_lengths = tf.placeholder(tf.int32, [None], "sentences_lengths")

        # Postcoder placeholders
        self.post_inputs = tf.placeholder(tf.int32, [None, None], "post_inputs")
        self.post_labels = tf.placeholder(tf.int32, [None, None], "post_labels")
        self.post_sentences_lengths = tf.placeholder(tf.int32, [None], "post_sentences_lengths")

        # Precoder placeholders
        self.pre_inputs = tf.placeholder(tf.int32, [None, None], "pre_inputs")
        self.pre_labels = tf.placeholder(tf.int32, [None, None], "pre_labels")
        self.pre_sentences_lengths = tf.placeholder(tf.int32, [None], "pre_sentences_lengths")

        # Embed sentences
        sentences_embedded = self.embed_data(self.sentences) 
        post_inputs_embedded = self.embed_data(self.post_inputs)
        pre_inputs_embedded = self.embed_data(self.pre_inputs)

        # Encoder
        self.encoded_sentences = self.encoder(sentences_embedded, self.sentences_lengths, self.para.bidirectional)

        # Decoder for following sentence
        post_logits_projected, post_logits = self.decoder(decoder_inputs = post_inputs_embedded, encoder_state = self.encoded_sentences, 
            name = 'postcoder', lengths = self.post_sentences_lengths, train = True)
        
        # Decoder for previous sentence
        pre_logits_projected, pre_logits = self.decoder(decoder_inputs = pre_inputs_embedded, encoder_state = self.encoded_sentences, 
            name = 'precoder', lengths = self.pre_sentences_lengths, train = True)
        
        # Compute loss
        if self.para.loss_function == 'softmax':
            post_loss = self.get_softmax_loss(self.post_labels, post_logits_projected) 
            pre_loss = self.get_softmax_loss(self.pre_labels, pre_logits_projected) 
        else:
            post_loss = self.get_sampled_softmax_loss(self.post_labels, post_logits, name='postcoder') 
            pre_loss = self.get_sampled_softmax_loss(self.pre_labels, pre_logits, name='precoder') 

        self.loss = pre_loss + post_loss
        self.opt_op = tf.contrib.layers.optimize_loss(loss = self.loss, global_step = self.global_step, learning_rate = self.para.learning_rate, 
            optimizer = 'Adam', clip_gradients=2.0, learning_rate_decay_fn=None, summaries = ['loss']) 

        # Decode sentences at prediction time
        pre_predict = self.decoder(decoder_inputs = pre_inputs_embedded, encoder_state = self.encoded_sentences, 
            name = 'precoder', lengths = self.pre_sentences_lengths, train = False)
        post_predict = self.decoder(decoder_inputs = post_inputs_embedded, encoder_state = self.encoded_sentences, 
            name = 'postcoder', lengths = self.post_sentences_lengths, train = False)
        self.predict = [pre_predict, post_predict]

    def embed_data(self, data):
        return tf.nn.embedding_lookup(self.word_embeddings, data)

    def encoder(self, sentences_embedded, sentences_lengths, bidirectional = False):
        with tf.variable_scope("encoder") as varscope:
            if bidirectional:
                cell = tf.contrib.rnn.GRUCell(self.para.hidden_size//2)
                cell = tf.contrib.rnn.DropoutWrapper(cell, input_keep_prob = self.para.keep_prob_dropout)
                cell = tf.contrib.rnn.MultiRNNCell([cell]*self.para.hidden_layers, state_is_tuple=True)
                print('Training bidirectional RNN')
                sentences_outputs, sentences_states = tf.nn.bidirectional_dynamic_rnn(cell, cell, 
                    inputs = sentences_embedded, sequence_length=sentences_lengths, dtype=tf.float32)
                states_fw, states_bw = sentences_states
                sentences_states_h = tf.concat([states_fw[-1],states_bw[-1]], axis = 1)
                # sentences_states_h = tf.contrib.layers.linear(sentences_states_h, self.para.hidden_size)
            else:
                cell = tf.contrib.rnn.GRUCell(self.para.hidden_size)
                cell = tf.contrib.rnn.DropoutWrapper(cell, input_keep_prob = self.para.keep_prob_dropout)
                cell = tf.contrib.rnn.MultiRNNCell([cell]*self.para.hidden_layers, state_is_tuple=True)
                print('Training one-directional RNN')
                sentences_outputs, sentences_states = tf.nn.dynamic_rnn(cell = cell, 
                    inputs = sentences_embedded, sequence_length=sentences_lengths, dtype=tf.float32)   
                sentences_states_h = sentences_states[-1]
        return sentences_states_h

    def decoder(self, decoder_inputs, encoder_state, name, lengths= None, train = True):
        dec_cell = tf.contrib.rnn.GRUCell(self.para.embedding_size)
        W = self.graph.get_tensor_by_name(name+'/weight:0')
        b = self.graph.get_tensor_by_name(name+'/bias:0')
        if train:
            with tf.variable_scope(name) as varscope:
                dynamic_fn_train = tf.contrib.seq2seq.simple_decoder_fn_train(encoder_state)
                outputs_train, state_train, _ = tf.contrib.seq2seq.dynamic_rnn_decoder(dec_cell, decoder_fn = dynamic_fn_train, 
                    inputs=decoder_inputs, sequence_length = lengths, scope = varscope)
                logits = tf.reshape(outputs_train, [-1, self.para.embedding_size])
                logits_train = tf.matmul(logits, W) + b
                logits_projected = tf.reshape(logits_train, [self.para.batch_size, tf.reduce_max(lengths), self.vocabulary_size])
                return logits_projected, outputs_train
        else:
            with tf.variable_scope(name, reuse = True) as varscope:
                output_fn = lambda x: tf.nn.softmax(tf.matmul(x, W) + b)
                dynamic_fn_inference = tf.contrib.seq2seq.simple_decoder_fn_inference(output_fn =output_fn, encoder_state = encoder_state, 
                    embeddings = self.word_embeddings, start_of_sequence_id = 2, end_of_sequence_id = 3, maximum_length = self.data.max_sent_len, num_decoder_symbols = self.vocabulary_size) 
                logits_inference, state_inference,_ = tf.contrib.seq2seq.dynamic_rnn_decoder(dec_cell, decoder_fn = dynamic_fn_inference, scope = varscope)
                return tf.arg_max(logits_inference, 2)

    def get_softmax_loss(self, labels, logits):
        return tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(labels=labels, logits=logits))

    def get_sampled_softmax_loss(self, labels, logits, name):
        W = self.graph.get_tensor_by_name(name+'/weight:0')
        b = self.graph.get_tensor_by_name(name+'/bias:0')
        logits = tf.stack(logits)
        logits_reshaped = tf.reshape(logits, [-1, self.para.embedding_size])
        labels_reshaped = tf.reshape(labels, [-1, 1])
        loss = tf.nn.sampled_softmax_loss(weights= tf.transpose(W), biases=b, labels=labels_reshaped, inputs = logits_reshaped, num_sampled = self.para.sampled_words, 
            num_classes = self.vocabulary_size, num_true=1)
        return tf.reduce_mean(loss)

    def print_sentence(self, sentence, length):
        s = ''
        for i in range(length):
            word = sentence[i]
            s = s+self.reverse_vocab[word]+' '
        return s

    def save_model(self, path, epoch):
        if not os.path.exists(path):
            os.mkdir(path)
        saver = tf.train.Saver()
        saver.save(self.session, path + '/epoch_%d.checkpoint' % epoch)

    def load_model(self, path):
        self.sess = tf.Session(graph = self.graph)
        saver = tf.train.Saver()
        logdir = tf.train.latest_checkpoint(path)
        saver.restore(self.sess, logdir)
        print('Model restored')

    def corpus_stats(self):
        print('\n~~~~~~~~~~~~~~~~~~~~~~~~~~~')
        print('Corpus name:', self.data.corpus_name)
        print('Vocabulary size:', len(self.sorted_vocab))
        # print('Number of sentences:', self.corpus_length)
        print('~~~~~~~~~~~~~~~~~~~~~~~~~~~\n')

    def evaluate(self, index = None):
        i = index if index != None else np.random.randint(len(self.data.enc_data))
        print('\nOriginal sequence:\n')
        print(self.print_sentence(self.data.pre_data[i, 1:], self.data.pre_lengths[i]-1))
        print(self.print_sentence(self.data.enc_data[i], self.data.enc_lengths[i]))
        print(self.print_sentence(self.data.post_data[i, 1:], self.data.post_lengths[i]-1))
        test_enc_lengths = np.expand_dims(self.data.enc_lengths[i], 0)
        test_enc_inputs = np.expand_dims(self.data.enc_data[i], 0)
        test_post_lengths = np.expand_dims(self.data.post_lengths[i], 0)
        test_post_inputs = np.expand_dims(self.data.post_data[i], 0)
        test_post_labels = np.expand_dims(self.data.post_lab[i], 0)
        test_pre_lengths = np.expand_dims(self.data.pre_lengths[i], 0)
        test_pre_inputs = np.expand_dims(self.data.pre_data[i], 0)
        test_pre_labels = np.expand_dims(self.data.pre_lab[i], 0)
        test_dict = {self.sentences_lengths: test_enc_lengths,
                    self.sentences: test_enc_inputs, 
                    self.post_sentences_lengths: test_post_lengths,
                    self.post_inputs: test_post_inputs,
                    self.post_labels: test_post_labels,
                    self.pre_sentences_lengths: test_pre_lengths,
                    self.pre_inputs: test_pre_inputs,
                    self.pre_labels: test_pre_labels}
        pre_prediction, post_prediction = self.sess.run([self.predict], feed_dict=test_dict)[0]
        print('\nPredicted previous and following sequence around original sentence:\n')
        print(self.print_sentence(pre_prediction[0], len(pre_prediction[0])))
        print(self.print_sentence(self.data.enc_data[i], self.data.enc_lengths[i]))
        print(self.print_sentence(post_prediction[0], len(post_prediction[0])))

    def encode(self, sentences, lengths):
        encode_dict = {self.sentences: sentences,
                       self.sentences_lengths: lengths}
        encoded_sentences = self.sess.run([self.encoded_sentences], feed_dict=encode_dict)
        return np.array(encoded_sentences)

    def initialise(self):
        # Save metadata for visualisation of embedding matrix
        # meta_data = sorted(self.dictionary, key=self.dictionary.get)
        # print(len(meta_data))
        # with open('meta_data.tsv', 'w') as f:
        #     tsv_writer = csv.writer(f, dialect='excel')
        #     tsv_writer.writerow(str(i.encode('utf-8')) +'\n' for i in meta_data)

        # Print summary statistics
        self.sess = tf.Session(graph = self.graph)
        # self.a= tf.contrib.graph_editor.get_tensors(self.graph)
        self.train_loss_writer = tf.summary.FileWriter('./tensorboard/train_loss', self.sess.graph)
        embedding_writer = tf.summary.FileWriter('./tensorboard/', self.sess.graph)
        config = projector.ProjectorConfig()
        embedding = config.embeddings.add()
        embedding.tensor_name = self.word_embeddings.name
        embedding.metadata_path = os.path.join('./meta_data.tsv')
        projector.visualize_embeddings(embedding_writer, config)
        self.merged = tf.summary.merge_all()
        print('\n~~~~~~~ Initializing variables ~~~~~~~\n')
        tf.global_variables_initializer().run(session = self.sess)

    def train(self):
        print('\n~~~~~~~ Starting training ~~~~~~~\n')
        start_time = time.time()
        try:
            train_summaryIndex = -1
            self.is_train = True
            perm = np.random.permutation(self.corpus_length)
            enc_lengths_perm = self.data.enc_lengths[perm]
            enc_data_perm = self.data.enc_data[perm]
            post_lengths_perm = self.data.post_lengths[perm]
            post_inputs_perm = np.array(self.data.post_data)[perm]
            post_labels_perm = np.array(self.data.post_lab)[perm]
            pre_lengths_perm = self.data.pre_lengths[perm]
            pre_inputs_perm = np.array(self.data.pre_data)[perm]
            pre_labels_perm = np.array(self.data.pre_lab)[perm]

            total_loss = 0
            predict_step = 5

            for step in range(self.corpus_length // self.para.batch_size):
                begin = step * self.para.batch_size
                end = (step + 1) * self.para.batch_size
                batch_enc_lengths = enc_lengths_perm[begin : end]
                batch_enc_inputs = enc_data_perm[begin : end]
                batch_post_lengths = post_lengths_perm[begin : end]
                batch_post_inputs = post_inputs_perm[begin:end, :np.max(batch_post_lengths)]
                batch_post_labels = post_labels_perm[begin:end, :np.max(batch_post_lengths)]
                batch_pre_lengths = pre_lengths_perm[begin : end]
                batch_pre_inputs = pre_inputs_perm[begin:end, :np.max(batch_pre_lengths)]
                batch_pre_labels = pre_labels_perm[begin:end, :np.max(batch_pre_lengths)]
                train_dict = {self.sentences_lengths: batch_enc_lengths,
                            self.sentences: batch_enc_inputs, 
                            self.post_sentences_lengths: batch_post_lengths,
                            self.post_inputs: batch_post_inputs,
                            self.post_labels: batch_post_labels,
                            self.pre_sentences_lengths: batch_pre_lengths,
                            self.pre_inputs: batch_pre_inputs,
                            self.pre_labels: batch_pre_labels}
                _, loss_val, batch_summary = self.sess.run([self.opt_op, self.loss, self.merged], feed_dict=train_dict)
                self.train_loss_writer.add_summary(batch_summary, step + (self.corpus_length // self.para.batch_size))
                total_loss += loss_val

                if self.global_step.eval(session = self.sess) % predict_step == 0:
                    print("Average loss at step ", self.global_step.eval(session = self.sess), ": ", total_loss/predict_step)
                    total_loss = 0
                    end_time = time.time()
                    print('\nTime for %d steps: %0.2f seconds' % (predict_step, end_time - start_time))
                    start_time = time.time()
                    self.evaluate()
                    print('\n\n~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
            saver = tf.train.Saver()
            saver.save(os.path.join(self.path + '/tensorboard/', 'model.ckpt'))
        except KeyboardInterrupt:
            save = input('save?')
            if 'y' in save:
                self.save_model(self.path + '/saved_models/', 0)

def initialise(corpus_name, input_path='./corpus/toronto_corpus/'):

    '''
    Needs to be run only once.
    This routine will create a vocabulary and skipthought data.
    input_path should contain .txt files with tokenised sentences, one per line.
    '''

    path = './models/skipthought_' + corpus_name +'/'
    output_path = './models/skipthought_' + corpus_name +'/training_data/'
    parts = glob.glob(input_path + '*.txt')

    if not os.path.exists(path):
        os.makedirs(path)
    if not os.path.exists(output_path):
        os.makedirs(output_path)
    
    if os.path.isfile(path + 'vocab.pkl'):
        with open('./models/skipthought_' + corpus_name + '/vocab.pkl', 'rb') as f:
            vocab = pkl.load(f)
        print('Vocab loaded')
    else:
        print('\nCreating vocab')
        vocab = defaultdict(int)
        for part in parts:
            vocab = word_vocab(part, vocab_name=part, vocab=vocab)
        with open(path + 'vocab.pkl', 'wb') as f:
            pkl.dump(vocab, f)
        print('\nVocab created')

    print('\n%d files to be prepared:' % len(parts), parts)
    i = 0
    for part in parts:
        data = get_training_data(part, vocab, corpus_name, 20)
        with open(path + 'training_data/data_%d.pkl' %i, 'wb') as f:
            pkl.dump(data, f)
        i+=1

def get_training_data(path, vocab, corpus_name, max_sent_len=20):

    '''
    Create datasets for encoder and decoders
    '''

    sent_lengths, max_sent_len, enc_data, dec_data, dec_lab = util.sent_to_int(path, dictionary=vocab, max_sent_len=max_sent_len, decoder=True)
    enc_lengths = sent_lengths[1:-1] 
    enc_data = enc_data[1:-1]
    post_lengths = sent_lengths[2:] + 1
    post_data = dec_data[2:]
    post_lab = dec_lab[2:]
    pre_lengths = sent_lengths[:-2] + 1
    pre_data = dec_data[:-2]
    pre_lab = dec_lab[:-2]
    return [corpus_name, max_sent_len, enc_lengths, enc_data, post_lengths, post_data, post_lab, pre_lengths, pre_data, pre_lab]

def train():
    tf.reset_default_graph()
    corpus = 'toronto'
    path = './models/skipthought_' + corpus
    with open(path + '/vocab.pkl', 'rb') as f:
        vocab = pkl.load(f)
    with open(path + corpus + '/training_data/data_1.pkl', 'rb') as f:
        data = pkl.load(f)

    paras = Skipthought_para(embedding_size = 500, 
        hidden_size = 500, 
        hidden_layers = 2, 
        batch_size = 32, 
        keep_prob_dropout = 1.0, 
        learning_rate = 0.01, 
        bidirectional = False,
        loss_function = 'sampled_softmax',
        sampled_words = 1000,
        num_epochs = 1)
    with open(path + '/paras.pkl', 'wb') as f:
        pkl.dump(paras, f)

    model = Skipthought_model(data = data, vocab = vocab, parameters = paras, path = path)
    model.initialise()
    data_parts = glob.glob(path + '/training_data/*.pkl')
    num_epochs = 10
    for epoch in range(num_epochs):
        print('----- Epoch', epoch, '-----')
        print('Shuffling dataset')
        for part in data_parts:
            with open(part, 'rb') as f:
                data = pkl.load(f)
            model.data = data
            model.train()
        model.save_model(model.path + '/saved_models/', epoch)

if __name__ == '__main__':

    initialise('toronto')
    # train()
    

    # model.load_model('./model/')
    # model.evaluate(1)
