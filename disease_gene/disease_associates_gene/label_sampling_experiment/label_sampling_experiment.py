#!/usr/bin/env python
# coding: utf-8

# # Using Labels from Different Relation Types to Predict Disease Associates Gene Sentences

# This notebook is designed to predict the disease associates gene (DaG) relation. The first step in this process is to load our pre-labeled annotation matricies (train, dev, and test). These matriceis contain sentences as the rows and the label function output as the columns (features). The working hypothesis here is there are shared information (i.e. similar keywords, same kind of sentence structure) between different relations, which in turn should aid in predicting disease associates gene relations. 
# 
# After loading the matricies, the next step is to train a generative model that will estimate the likelihood of the positive class: $P(\hat{Y}=1 \mid \text{label functions})$. The generative model does this by estimating the parameter $\mu$. This parameter represents the probability of a label function given the true class: $ P(\text{label function={0,1,2}} \mid Y={1 (+) / 0 (-)})$. Once $\mu$ has been estimated, calculating the likelihood becomes: $P(\hat{Y}=1 \mid \text{label functions}) = (\prod_{i=1}^{N} P(\text{label function}_{i} = 1 \mid \text{Y} = 1)) * P(Y = 1)$
# 
# Note: This process doesn't involve any sentence context, so the only information used here are categorical output.

# ## Set up the environment and load the data

# In[1]:


get_ipython().run_line_magic('load_ext', 'autoreload')
get_ipython().run_line_magic('autoreload', '2')
get_ipython().run_line_magic('matplotlib', 'inline')

import glob
import sys
import os
sys.path.append(os.path.abspath('../../../modules'))

import pandas as pd
from sklearn.metrics import (
    precision_recall_curve, 
    precision_recall_fscore_support, 
    confusion_matrix, 
    auc, roc_curve, 
    average_precision_score
)
from utils.notebook_utils.train_model_helper import (
    get_model_performance, 
    train_model_random_lfs, 
    sample_lfs
)
from utils.notebook_utils.dataframe_helper import load_candidate_dataframes

import plotnine
import seaborn as sns
import logging

logging.basicConfig(filename='logs.log', level='INFO')


# In[2]:


label_destinations = {
    'train':"../data/label_matricies/train_sparse_matrix.tsv.xz",
    'dev':"../data/label_matricies/dev_sparse_matrix.tsv.xz",
    'test':"../data/label_matricies/test_sparse_matrix.tsv.xz"
}
label_matricies = {
    key:pd.read_csv(label_destinations[key], sep="\t").to_sparse()
    for key in label_destinations
}


# In[36]:


# Majority of this code was using snorkel metal
# Metal use the following encoding for class representation: 0 - abstain, 1 - positive, class 2 - negative class
# Upgraded snorkel changed their encoding to be: -1 - abstain, 0 - negative class, 1 - positive class
# To save time I'm just converting the label matricies to match the new coding scheme
label_matricies['train'] = label_matricies['train'].fillna(-2).to_dense().replace({-1:0, -2:-1})
label_matricies['dev'] = label_matricies['dev'].fillna(-2).to_dense().replace({-1:0, -2:-1})
label_matricies['test'] = label_matricies['test'].fillna(-2).to_dense().replace({-1:0, -2:-1})


# In[4]:


correct_L = label_matricies['train'].drop("candidate_id", axis=1).to_numpy()
correct_L_dev = label_matricies['dev'].drop("candidate_id", axis=1).to_numpy()
correct_L_test = label_matricies['test'].drop("candidate_id", axis=1).to_numpy()


# In[5]:


spreadsheet_names = {
    #'train': 'data/sentences/sentence_labels_train.xlsx',
    'dev': '../data/sentences/sentence_labels_dev.xlsx',
    'test': '../data/sentences/sentence_labels_test.xlsx'
}


# In[6]:


candidate_dfs = {
    key:load_candidate_dataframes(spreadsheet_names[key], "curated_dsh")
    for key in spreadsheet_names
}

for key in candidate_dfs:
    print("Size of {} set: {}".format(key, candidate_dfs[key].shape[0]))


# ## Train the baseline

# The baseline model for this experiment is the generative model trained on databases alone (distant supervision). A common solution towards the lack of labels problem is to use databases to effectively label sentences. The caveat is the approach doesn't take sentence context into account, which results in high recall but low precision of targeted relations. By training this model we expect that performance will be modest (~0.5 +/- 0.1 AUROC).

# In[7]:


baseline_index = list(range(0,7))
train_grid_results, dev_grid_results, test_grid_results, models = (
    train_model_random_lfs(
        [baseline_index], correct_L, 
        correct_L_dev, candidate_dfs['dev'].curated_dsh, correct_L_test, 
        pd.np.round(pd.np.linspace(0.01, 5, num=5), 2)
    )
)


# In[8]:


(
    pd.DataFrame({
        key:train_grid_results[key][:,1] 
        for key in train_grid_results
    })
    .assign(candidate_id=label_matricies['train'].candidate_id.values)
    .to_csv(f"output/DaG/marginals/baseline_sampled.tsv.xz", compression="xz", sep="\t", index=False)
)


# In[9]:


dev_baseline_df = get_model_performance(candidate_dfs['dev'].curated_dsh, dev_grid_results, num_lfs=0)
dev_baseline_df.head(2)


# In[10]:


test_baseline_df = get_model_performance(candidate_dfs['test'].curated_dsh, test_grid_results, num_lfs=0)
test_baseline_df.head(2)


# # Disease Associates Gene Sources Predicts Disease Associates Gene Sentences

# Here we are using label functions, designed to predict the Disease Associates Gene relation, to predict Disease Associates Gene sentences. To estimate the performance boost over the baseline model, we implement a label function sampling appoach. The sampling approach works as follows: 
# 1. randomly sample X amount of label functions that are not within the database category
# 2. incorporate the sampled label functions with the database label functions
# 3. train the generative model on the combined resources
# 4. use the generative model to predict the tuning set and test set
# 5. Report performance in terms of AUROC and AUPR
# 6. repeat the above process 50 times for each sample size (1, 6, 11, 16, all).
# 
# Given that these label functions are designed to predict the given relation, we expect that adding more label functions will increase in performance. This means that auroc when sampling 1 label function should be greater than the auroc of the baseline. This trend should continue when sampling 6, 11, 16 and then all of the label functions.

# In[11]:


dag_start = 7
dag_end = 37

#Spaced out number of sampels including total
size_of_samples = [1,6,11,16,dag_end-dag_start]
number_of_samples = 50
dag_lf_range = range(dag_start, dag_end)


# In[12]:


sampled_lfs_dict = {
    sample_size:(
        sample_lfs(
            list(dag_lf_range),
            len(list(dag_lf_range)), 
            sample_size, 
            number_of_samples, 
            random_state=100
        )
    )
    for sample_size in size_of_samples
}


# In[13]:


dev_records = []
test_records = []
for num_lf in sampled_lfs_dict:
    train_grid_results, dev_grid_results, test_grid_results, models = (
        train_model_random_lfs(
            [baseline_index + sample for sample in sampled_lfs_dict[num_lf]],
            correct_L, correct_L_dev, candidate_dfs['dev'].curated_dsh,
            correct_L_test, pd.np.round(pd.np.linspace(0.01, 5, num=5), 2)
        )
    )
    
    (
        pd.DataFrame({key:train_grid_results[key][:,1] for key in train_grid_results})
        .assign(candidate_id=label_matricies['train'].candidate_id.values)
        .to_csv(f"output/DaG/marginals/{num_lf}_sampled_train.tsv.xz", compression="xz", index=False, sep="\t")
    )
    (
        pd.DataFrame({key:dev_grid_results[key][:,1] for key in dev_grid_results})
        .to_csv(f"output/DaG/marginals/{num_lf}_sampled_dev.tsv", index=False, sep="\t")
    )
    (
        pd.DataFrame({key:test_grid_results[key][:,1] for key in test_grid_results})
        .to_csv(f"output/DaG/marginals/{num_lf}_sampled_test.tsv", index=False, sep="\t")
    )
    
    (
        pd.DataFrame({key:models[key].get_weights() for key in models})
        .to_csv(f"output/DaG/weights/{num_lf}_sampled_weights.tsv", index=False, sep="\t")
    )
    
    dev_records.append(get_model_performance(candidate_dfs['dev'].curated_dsh, dev_grid_results, num_lf))
    test_records.append(get_model_performance(candidate_dfs['test'].curated_dsh, test_grid_results, num_lf))


# In[14]:


dev_full_results_df = pd.concat([dev_baseline_df] + dev_records).reset_index(drop=True)
dev_full_results_df.to_csv("output/DaG/results/dev_sampled_results.tsv", index=False, sep="\t")
dev_full_results_df.head(2)


# In[43]:


sns.pointplot(x='lf_num', y='auroc', data=dev_full_results_df)


# In[44]:


sns.pointplot(x='lf_num', y='aupr', data=dev_full_results_df)


# In[15]:


test_full_results_df = pd.concat([test_baseline_df] + test_records).reset_index(drop=True)
test_full_results_df.to_csv("output/DaG/results/test_sampled_results.tsv", index=False, sep="\t")
test_full_results_df.head(2)


# In[45]:


sns.pointplot(x='lf_num', y='auroc', data=test_full_results_df)


# In[46]:


sns.pointplot(x='lf_num', y='aupr', data=dev_full_results_df)


# # Compound Treats Disease Sources Predicts Disease Associates Gene Sentences

# Here we are using label functions, designed to predict the Compond treats Disease relation, to predict Disease Associates Gene sentences. To estimate the performance boost over the baseline model, we implement a label function sampling appoach. The sampling approach works as follows: 
# 1. randomly sample X amount of label functions that are not within the database category
# 2. incorporate the sampled label functions with the database label functions
# 3. train the generative model on the combined resources
# 4. use the generative model to predict the tuning set and test set
# 5. Report performance in terms of AUROC and AUPR
# 6. repeat the above process 50 times for each sample size (1, 6, 11, 16, all).
# 
# Given that these label functions are not designed to predict the given relation, we expect that adding more label functions will decrease in performance. This means that auroc when sampling 1 label function should be less than the auroc of the baseline. This trend should continue when sampling 6, 11, 16 and then all of the label functions.

# In[16]:


ctd_start = 37
ctd_end = 59

#Spaced out number of sampels including total
size_of_samples = [1,6,11,16,ctd_end-ctd_start]
number_of_samples = 50
ctd_lf_range = range(ctd_start, ctd_end)


# In[17]:


sampled_lfs_dict = {
    sample_size:(
        sample_lfs(
            list(ctd_lf_range),
            len(list(ctd_lf_range)), 
            sample_size, 
            number_of_samples, 
            random_state=100
        )
    )
    for sample_size in size_of_samples
}


# In[18]:


dev_records = []
test_records = []
for num_lf in sampled_lfs_dict:
    train_grid_results, dev_grid_results, test_grid_results, models = (
        train_model_random_lfs(
            [baseline_index + sample for sample in sampled_lfs_dict[num_lf]],
            correct_L, correct_L_dev, candidate_dfs['dev'].curated_dsh, 
            correct_L_test,pd.np.round(pd.np.linspace(0.01, 5, num=5), 2)
        )
    )
    
    (
        pd.DataFrame({key:train_grid_results[key][:,1] for key in train_grid_results})
        .assign(candidate_id=label_matricies['train'].candidate_id.values)
        .to_csv(f"output/CtD/marginals/{num_lf}_sampled_train.tsv.xz", compression="xz", index=False, sep="\t")
    )
    (
        pd.DataFrame({key:dev_grid_results[key][:,1] for key in dev_grid_results})
        .to_csv(f"output/CtD/marginals/{num_lf}_sampled_dev.tsv", index=False, sep="\t")
    )
    (
        pd.DataFrame({key:test_grid_results[key][:,1] for key in test_grid_results})
        .to_csv(f"output/CtD/marginals/{num_lf}_sampled_test.tsv", index=False, sep="\t")
    )
    
    (
        pd.DataFrame({key:models[key].get_weights() for key in models})
        .to_csv(f"output/CtD/weights/{num_lf}_sampled_weights.tsv", index=False, sep="\t")
    )
    
    dev_records.append(get_model_performance(candidate_dfs['dev'].curated_dsh, dev_grid_results, num_lf))
    test_records.append(get_model_performance(candidate_dfs['test'].curated_dsh, test_grid_results, num_lf))


# In[19]:


dev_full_results_df = pd.concat([dev_baseline_df] + dev_records).reset_index(drop=True)
dev_full_results_df.to_csv("output/CtD/results/dev_sampled_results.tsv", index=False, sep="\t")
dev_full_results_df.head(2)


# In[48]:


sns.pointplot(x='lf_num', y='auroc', data=dev_full_results_df)


# In[49]:


sns.pointplot(x='lf_num', y='aupr', data=dev_full_results_df)


# In[20]:


test_full_results_df = pd.concat([test_baseline_df] + test_records).reset_index(drop=True)
test_full_results_df.to_csv("output/CtD/results/test_sampled_results.tsv", index=False, sep="\t")
test_full_results_df.head(2)


# In[50]:


sns.pointplot(x='lf_num', y='auroc', data=test_full_results_df)


# In[51]:


sns.pointplot(x='lf_num', y='aupr', data=test_full_results_df)


# # Compound Binds Gene Sources Predicts Disease Associates Gene Sentences

# Here we are using label functions, designed to predict the Compond binds Gene relation, to predict Disease Associates Gene sentences. To estimate the performance boost over the baseline model, we implement a label function sampling appoach. The sampling approach works as follows: 
# 1. randomly sample X amount of label functions that are not within the database category
# 2. incorporate the sampled label functions with the database label functions
# 3. train the generative model on the combined resources
# 4. use the generative model to predict the tuning set and test set
# 5. Report performance in terms of AUROC and AUPR
# 6. repeat the above process 50 times for each sample size (1, 6, 11, 16, all).
# 
# Given that these label functions are not designed to predict the given relation, we expect that adding more label functions will decrease in performance. This means that auroc when sampling 1 label function should be less than the auroc of the baseline. This trend should continue when sampling 6, 11, 16 and then all of the label functions.

# In[21]:


cbg_start = 59
cbg_end = 79

#Spaced out number of sampels including total
size_of_samples = [1,6,11,16,cbg_end-cbg_start]
number_of_samples = 50
cbg_lf_range = range(cbg_start, cbg_end)


# In[22]:


sampled_lfs_dict = {
    sample_size:(
        sample_lfs(
            list(cbg_lf_range),
            len(list(cbg_lf_range)), 
            sample_size, 
            number_of_samples, 
            random_state=100
        )
    )
    for sample_size in size_of_samples
}


# In[23]:


dev_records = []
test_records = []
for num_lf in sampled_lfs_dict:
    train_grid_results, dev_grid_results, test_grid_results, models = (
        train_model_random_lfs(
            [baseline_index + sample for sample in sampled_lfs_dict[num_lf]],
            correct_L, correct_L_dev, candidate_dfs['dev'].curated_dsh,
            correct_L_test,pd.np.round(pd.np.linspace(0.01, 5, num=5), 2)
        )
    )
    
    (
        pd.DataFrame({key:train_grid_results[key][:,1] for key in train_grid_results})
        .assign(candidate_id=label_matricies['train'].candidate_id.values)
        .to_csv(f"output/CbG/marginals/{num_lf}_sampled_train.tsv.xz", compression="xz", index=False, sep="\t")
    )
    (
        pd.DataFrame({key:dev_grid_results[key][:,1] for key in dev_grid_results})
        .to_csv(f"output/CbG/marginals/{num_lf}_sampled_dev.tsv", index=False, sep="\t")
    )
    (
        pd.DataFrame({key:test_grid_results[key][:,1] for key in test_grid_results})
        .to_csv(f"output/CbG/marginals/{num_lf}_sampled_test.tsv", index=False, sep="\t")
    )
    
    (
        pd.DataFrame({key:models[key].get_weights() for key in models})
        .to_csv(f"output/CbG/weights/{num_lf}_sampled_weights.tsv", index=False, sep="\t")
    )
    
    dev_records.append(get_model_performance(candidate_dfs['dev'].curated_dsh, dev_grid_results, num_lf))
    test_records.append(get_model_performance(candidate_dfs['test'].curated_dsh, test_grid_results, num_lf))


# In[24]:


dev_full_results_df = pd.concat([dev_baseline_df] + dev_records).reset_index(drop=True)
dev_full_results_df.to_csv("output/CbG/results/dev_sampled_results.tsv", index=False, sep="\t")
dev_full_results_df.head(2)


# In[53]:


sns.pointplot(x='lf_num', y='auroc', data=dev_full_results_df)


# In[54]:


sns.pointplot(x='lf_num', y='aupr', data=dev_full_results_df)


# In[25]:


test_full_results_df = pd.concat([test_baseline_df] + test_records).reset_index(drop=True)
test_full_results_df.to_csv("output/CbG/results/test_sampled_results.tsv", index=False, sep="\t")
test_full_results_df.head(2)


# In[55]:


sns.pointplot(x='lf_num', y='auroc', data=test_full_results_df)


# In[56]:


sns.pointplot(x='lf_num', y='aupr', data=test_full_results_df)


# # Gene Interacts Gene Sources Predicts Disease Associates Gene Sentences

# Here we are using label functions, designed to predict the Gene interacts Gene relation, to predict Disease Associates Gene sentences. To estimate the performance boost over the baseline model, we implement a label function sampling appoach. The sampling approach works as follows: 
# 1. randomly sample X amount of label functions that are not within the database category
# 2. incorporate the sampled label functions with the database label functions
# 3. train the generative model on the combined resources
# 4. use the generative model to predict the tuning set and test set
# 5. Report performance in terms of AUROC and AUPR
# 6. repeat the above process 50 times for each sample size (1, 6, 11, 16, all).
# 
# Given that these label functions are not designed to predict the given relation, we expect that adding more label functions will decrease in performance. This means that auroc when sampling 1 label function should be less than the auroc of the baseline. This trend should continue when sampling 6, 11, 16 and then all of the label functions.

# In[26]:


gig_start = 79
gig_end = 107

#Spaced out number of sampels including total
size_of_samples = [1,6,11,16,gig_end-gig_start]
number_of_samples = 50
gig_lf_range = range(gig_start, gig_end)


# In[27]:


sampled_lfs_dict = {
    sample_size:(
        sample_lfs(
            list(gig_lf_range),
            len(list(gig_lf_range)), 
            sample_size, 
            number_of_samples, 
            random_state=100
        )
    )
    for sample_size in size_of_samples
}


# In[28]:


dev_records = []
test_records = []
for num_lf in sampled_lfs_dict:
    train_grid_results, dev_grid_results, test_grid_results, models = (
        train_model_random_lfs(
            [baseline_index + sample for sample in sampled_lfs_dict[num_lf]], 
            correct_L, correct_L_dev, candidate_dfs['dev'].curated_dsh, 
            correct_L_test, pd.np.round(pd.np.linspace(0.01, 5, num=5), 2)
        )
    )
    
    (
        pd.DataFrame({key:train_grid_results[key][:,1] for key in train_grid_results})
        .assign(candidate_id=label_matricies['train'].candidate_id.values)
        .to_csv(f"output/GiG/marginals/{num_lf}_sampled_train.tsv.xz", compression="xz", sep="\t", index=False)
    )
    (
        pd.DataFrame({key:dev_grid_results[key][:,1] for key in dev_grid_results})
        .to_csv(f"output/GiG/marginals/{num_lf}_sampled_dev.tsv", sep="\t", index=False)
    )
    (
        pd.DataFrame({key:test_grid_results[key][:,1] for key in test_grid_results})
        .to_csv(f"output/GiG/marginals/{num_lf}_sampled_test.tsv", sep="\t", index=False)
    )
    
    (
        pd.DataFrame({key:models[key].get_weights() for key in models})
        .to_csv(f"output/GiG/weights/{num_lf}_sampled_weights.tsv", sep="\t", index=False)
    )
    
    dev_records.append(get_model_performance(candidate_dfs['dev'].curated_dsh, dev_grid_results, num_lf))
    test_records.append(get_model_performance(candidate_dfs['test'].curated_dsh, test_grid_results, num_lf))


# In[29]:


dev_full_results_df = pd.concat([dev_baseline_df] + dev_records).reset_index(drop=True)
dev_full_results_df.to_csv("output/GiG/results/dev_sampled_results.tsv", index=False, sep="\t")
dev_full_results_df.head(2)


# In[59]:


sns.pointplot(x='lf_num', y='auroc', data=dev_full_results_df)


# In[60]:


sns.pointplot(x='lf_num', y='aupr', data=dev_full_results_df)


# In[30]:


test_full_results_df = pd.concat([test_baseline_df] + test_records).reset_index(drop=True)
test_full_results_df.to_csv("output/GiG/results/test_sampled_results.tsv", index=False, sep="\t")
test_full_results_df.head(2)


# In[61]:


sns.pointplot(x='lf_num', y='auroc', data=test_full_results_df)


# In[62]:


sns.pointplot(x='lf_num', y='aupr', data=test_full_results_df)


# # All Sources Predicts Gene Interacts Gene Sentences

# Here we are using every hand constructed label function to predict Disease Associates Gene sentences. To estimate the performance boost over the baseline model, we implement a label function sampling appoach. The sampling approach works as follows: 
# 1. randomly sample X amount of label functions that are not within the database category
# 2. incorporate the sampled label functions with the database label functions
# 3. train the generative model on the combined resources
# 4. use the generative model to predict the tuning set and test set
# 5. Report performance in terms of AUROC and AUPR
# 6. repeat the above process 50 times for each sample size (1, 33, 65, 97, all).
# 
# Given that some of these label functions are used to predict the given relation, we expect that adding more label functions might slightly increase performance. This means that auroc when sampling 1 label function should be higher than the auroc of the baseline; however, at 33, 65, 97 the auroc should start to decrease as we are adding more irrelevant label functions towards the baseline model.

# In[31]:


all_start = 7
all_end = 107

#Spaced out number of sampels including total
size_of_samples = [1,33,65,97,all_end-all_start]
number_of_samples = 50
cbg_lf_range = range(all_start, all_end)


# In[32]:


sampled_lfs_dict = {
    sample_size:(
        sample_lfs(
            list(cbg_lf_range),
            len(list(cbg_lf_range)), 
            sample_size, 
            number_of_samples, 
            random_state=100
        )
    )
    for sample_size in size_of_samples
}


# In[33]:


dev_records = []
test_records = []
for num_lf in sampled_lfs_dict:
    train_grid_results, dev_grid_results, test_grid_results, models = (
        train_model_random_lfs(
            [baseline_index + sample for sample in sampled_lfs_dict[num_lf]],
            correct_L, correct_L_dev, candidate_dfs['dev'].curated_dsh,
            correct_L_test, pd.np.round(pd.np.linspace(0.01, 5, num=5), 2)
        )
    )
    
    (
        pd.DataFrame({key:train_grid_results[key][:,1] for key in train_grid_results})
        .assign(candidate_id=label_matricies['train'].candidate_id.values)
        .to_csv(f"output/all/marginals/{num_lf}_sampled_train.tsv.xz", compression="xz", index=False, sep="\t")
    )
    (
        pd.DataFrame({key:dev_grid_results[key][:,1] for key in dev_grid_results})
        .to_csv(f"output/all/marginals/{num_lf}_sampled_dev.tsv", index=False, sep="\t")
    )
    (
        pd.DataFrame({key:test_grid_results[key][:,1] for key in test_grid_results})
        .to_csv(f"output/all/marginals/{num_lf}_sampled_test.tsv", index=False, sep="\t")
    )
    
    (
        pd.DataFrame({key:models[key].get_weights() for key in models})
        .to_csv(f"output/all/weights/{num_lf}_sampled_weights.tsv", index=False, sep="\t")
    )
    
    dev_records.append(get_model_performance(candidate_dfs['dev'].curated_dsh, dev_grid_results, num_lf))
    test_records.append(get_model_performance(candidate_dfs['test'].curated_dsh, test_grid_results, num_lf))


# In[34]:


dev_full_results_df = pd.concat([dev_baseline_df] + dev_records).reset_index(drop=True)
dev_full_results_df.to_csv("output/all/results/dev_sampled_results.tsv", index=False, sep="\t")
dev_full_results_df.head(2)


# In[38]:


sns.pointplot(x='lf_num', y='auroc', data=dev_full_results_df)


# In[39]:


sns.pointplot(x='lf_num', y='aupr', data=dev_full_results_df)


# In[35]:


test_full_results_df = pd.concat([test_baseline_df] + test_records).reset_index(drop=True)
test_full_results_df.to_csv("output/all/results/test_sampled_results.tsv", index=False, sep="\t")
test_full_results_df.head(2)


# In[40]:


sns.pointplot(x='lf_num', y='auroc', data=test_full_results_df)


# In[41]:


sns.pointplot(x='lf_num', y='aupr', data=test_full_results_df)

