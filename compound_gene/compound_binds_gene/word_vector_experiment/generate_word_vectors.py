#!/usr/bin/env python
# coding: utf-8

# # Generate Word Vectors For Compound Binds Gene Sentences

# This notebook is designed to generate word vectors for compound binds gene (CbG) sentences. Using facebooks's fasttext, we trained word vectors using all sentences that contain a disease and gene mention. The model was trained using the following specifications:
# 
# | Parameter | Value |
# | --- | --- |
# | Size | 300 |
# | alpha | 0.005 | 
# | window | 2 |
# | epochs | 50 |
# | seed | 100 | 

# # Set Up Environment

# In[1]:


get_ipython().run_line_magic('load_ext', 'autoreload')
get_ipython().run_line_magic('autoreload', '2')
get_ipython().run_line_magic('matplotlib', 'inline')

from collections import defaultdict
import os
import pickle
import sys

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from tqdm import tqdm_notebook

from gensim.models import FastText
from gensim.models import KeyedVectors

sys.path.append(os.path.abspath('../../../modules'))

from utils.notebook_utils.dataframe_helper import load_candidate_dataframes, generate_embedded_df


# In[2]:


#Set up the environment
username = "danich1"
password = "snorkel"
dbname = "pubmeddb"

#Path subject to change for different os
database_str = "postgresql+psycopg2://{}:{}@/{}?host=/var/run/postgresql".format(username, password, dbname)
os.environ['SNORKELDB'] = database_str

from snorkel import SnorkelSession
session = SnorkelSession()


# In[3]:


from snorkel.learning.pytorch.rnn.rnn_base import mark_sentence
from snorkel.learning.pytorch.rnn.utils import candidate_to_tokens
from snorkel.models import Candidate, candidate_subclass


# In[4]:


CompoundGene = candidate_subclass('CompoundGene', ['Compound', 'Gene'])


# # Compound Binds Gene

# This section loads the dataframe that contains all compound binds gene candidate sentences and their respective dataset assignments.

# In[5]:


cutoff = 300
total_candidates_df = (
    pd.read_table("../dataset_statistics/data/all_cbg_candidates.tsv.xz")
    .query("sen_length < @cutoff")
)
total_candidates_df.head(2)


# # Train Word Vectors

# This section trains the word vectors using the specifications described above.

# In[10]:


words_to_embed = []
candidates = (
    session
    .query(CompoundGene)
    .filter(
        CompoundGene.id.in_(
            total_candidates_df
            .candidate_id
            .astype(int)
            .tolist()
        )
    )
    .all()
)


# In[11]:


for cand in tqdm_notebook(candidates):
    args = [
                (cand[0].get_word_start(), cand[0].get_word_end(), 1),
                (cand[1].get_word_start(), cand[1].get_word_end(), 2)
    ]
    words_to_embed.append(mark_sentence(candidate_to_tokens(cand), args))


# In[12]:


model = FastText(
    words_to_embed, 
    window=2, 
    negative=10, 
    iter=50, 
    sg=1, 
    workers=4, 
    alpha=0.005, 
    size=300, 
    seed=100
)


# In[13]:


(
    model
    .wv
    .save_word2vec_format(
        "output/compound_gene_word_vectors.bin", 
        fvocab="output/compound_gene_word_vocab.txt", 
        binary=False
        )
)


# In[14]:


model.wv.most_similar("diabetes")


# In[15]:


word_dict = {val[1]:val[0] for val in list(enumerate(model.wv.vocab.keys()))}
word_dict_df = (
    pd
    .DataFrame
    .from_dict(word_dict, orient="index")
    .reset_index()
    .rename({"index":"word", 0:"index"}, axis=1)
)
word_dict_df.to_csv("output/compound_gene_word_dict.tsv", sep="\t", index=False)
word_dict_df.head(2)


# # Embed all Compound Binds Gene Sentences

# **Note**: Must run this section separately because the kernel cannot handle both training the word vectors and then embedding each CbG sentence.
# 
# This section embesd all candidate sentences. For each sentence, we place tags around each mention, tokenized the sentence and then matched each token to their corresponding word index. Any words missing from our vocab receive a index of 1. Lastly, the embedded sentences are exported as a sparse dataframe.

# In[6]:


word_dict_df = pd.read_table("output/compound_gene_word_dict.tsv")
word_dict = {word[0]:word[1] for word in word_dict_df.values.tolist()}


# In[ ]:


limit = 500000 #1000000
total_candidate_count = total_candidates_df.shape[0]

for offset in list(range(0, total_candidate_count, limit)):
    candidates = (
        session
        .query(CompoundGene)
        .filter(
            CompoundGene.id.in_(
                total_candidates_df
                .candidate_id
                .astype(int)
                .tolist()
            )
        )
        .offset(offset)
        .limit(limit)
        .all()
    )
    
    max_length = total_candidates_df.sen_length.max()
    
    # if first iteration create the file
    if offset == 0:
        (
            generate_embedded_df(candidates, word_dict, max_length=max_length)
            .to_csv(
                "output/all_embedded_cg_sentences.tsv",
                index=False, 
                sep="\t",
                mode="w"
            )
        )
        
    # else append don't overwrite
    else:
        (
            generate_embedded_df(candidates, word_dict, max_length=max_length)
            .to_csv(
                "output/all_embedded_cg_sentences.tsv",
                index=False, 
                sep="\t", 
                mode="a",
                header=False
            )
        )


# In[ ]:


os.system("cd output; xz all_embedded_cg_sentences.tsv")

