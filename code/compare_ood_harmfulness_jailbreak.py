import os
import json
import csv
import pandas as pd
import numpy as np
import argparse
from transformers import AutoTokenizer, AutoModelForCausalLM, set_seed, AutoConfig
import torch
import logging
from tqdm import tqdm
from scipy.stats import ttest_1samp
import warnings
from utils import patch_open, logging_cuda_memory_usage, get_following_indices
from safetensors import safe_open
import gc
import random
from matplotlib import pyplot as plt
from sklearn.decomposition import PCA
from utils import PCA_DIM


logging.basicConfig(
    format="[%(asctime)s] [%(filename)s:%(lineno)d] %(message)s",
    level=logging.INFO,
)
warnings.simplefilter("ignore")


def calculate_boundary(xlim, ylim, weight, bias):
    if np.abs(weight[0]) > np.abs(weight[1]):
        xlim_by_ylim_0 = (-bias - weight[1] * ylim[0]) / weight[0]
        xlim_by_ylim_1 = (-bias - weight[1] * ylim[1]) / weight[0]
        return [(xlim_by_ylim_0, ylim[0]), (xlim_by_ylim_1, ylim[1])]
    else:
        ylim_by_xlim_0 = (-bias - weight[0] * xlim[0]) / weight[1]
        ylim_by_xlim_1 = (-bias - weight[0] * xlim[1]) / weight[1]
        return [(xlim[0], ylim_by_xlim_0), (xlim[1], ylim_by_xlim_1)]


def main():
    patch_open()

    parser = argparse.ArgumentParser()
    parser.add_argument("--pretrained_model_paths", type=str, nargs='+', required=True)
    parser.add_argument("--config", type=str, choices=["greedy", "sampling"])
    parser.add_argument("--output_path", type=str, required=True)
    args = parser.parse_args()

    # prepare data
    fname = f'all_soft_harmfulness'
    dataset_1 = 'advbench'
    fname += f'_{dataset_1}'
    dataset_2 = 'malicious'
    fname += f'_{dataset_2}'
    dataset_harmless = 'testset'
    with open(f"./data/advbench.txt") as f:
        lines_1 = f.readlines()[:100]
    with open(f"data/MaliciousInstruct.txt") as f:
        lines_2 = f.readlines()
    with open(f"./data_harmless/testset.txt") as f:
        lines_harmless = f.readlines()
    os.makedirs(args.output_path, exist_ok=True)

    #colors = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    colors = {
        'held-out': 'tab:blue',
        'held-out + jailbreak': 'tab:cyan',
        'malicious': 'tab:red',
        'malicious + jailbreak': 'tab:pink',
        'advbench': 'tab:brown',
        'advbench + jailbreak': 'tab:orange',
    }

    all_queries_1 = [e.strip() for e in lines_1 if e.strip()]
    all_queries_2 = [e.strip() for e in lines_2 if e.strip()]
    n_queries = len(all_queries_1)

    all_queries_harmless = [e.strip() for e in lines_harmless if e.strip()]
    n_queries_harmless = len(all_queries_harmless)

    ncols = 1
    if len(args.pretrained_model_paths) % ncols != 0:
        raise ValueError(f"len(args.pretrained_model_paths) % ncols != 0")
    nrows = len(args.pretrained_model_paths) // ncols
    fig = plt.figure(figsize=(4.5 * ncols, 3.8 * nrows))

    for mdx, pretrained_model_path in enumerate(args.pretrained_model_paths):
        logging_cuda_memory_usage()
        torch.cuda.empty_cache()
        gc.collect()

        logging.info(pretrained_model_path)

        # prepare model
        model_name = pretrained_model_path.split('/')[-1]
        config = AutoConfig.from_pretrained(pretrained_model_path)
        num_layers = config.num_hidden_layers


        # w/o
        logging.info(f"Running w/o")
        hidden_states = safe_open(f'hidden_states_harmless/{model_name}_{dataset_harmless}.safetensors',
                                  framework='pt', device=0)
        all_hidden_states_harmless = []
        for idx, query in enumerate(all_queries_1):
            tmp_hidden_states = hidden_states.get_tensor(f'sample.{idx}_layer.{num_layers-1}')[-1]
            all_hidden_states_harmless.append(tmp_hidden_states)

        hidden_states = safe_open(f'hidden_states/{model_name}_{dataset_1}.safetensors',
                                  framework='pt', device=0)
        all_hidden_states_1 = []
        for idx, query in enumerate(all_queries_1):
            tmp_hidden_states = hidden_states.get_tensor(f'sample.{idx}_layer.{num_layers-1}')[-1]
            all_hidden_states_1.append(tmp_hidden_states)

        hidden_states = safe_open(f'hidden_states/{model_name}_{dataset_2}.safetensors',
                                  framework='pt', device=0)
        all_hidden_states_2 = []
        for idx, query in enumerate(all_queries_2):
            tmp_hidden_states = hidden_states.get_tensor(f'sample.{idx}_layer.{num_layers-1}')[-1]
            all_hidden_states_2.append(tmp_hidden_states)

        all_hidden_states_1 = torch.stack(all_hidden_states_1)
        all_hidden_states_2 = torch.stack(all_hidden_states_2)
        all_hidden_states_harmless = torch.stack(all_hidden_states_harmless)

        indices_1, other_indices_1 = get_following_indices(
            model_name, dataset_1, config=args.config, use_harmless=False)
        indices_2, other_indices_2 = get_following_indices(
            model_name, dataset_2, config=args.config, use_harmless=False)
        indices_harmless, other_indices_harmless = get_following_indices(
            model_name, dataset_harmless, config=args.config, use_harmless=True)


        # jailbreak
        logging.info(f"Running jailbreak")
        hidden_states_with_jailbreak = safe_open(f'hidden_states_with_jailbreak-v2_harmless/{model_name}_{dataset_harmless}.safetensors',
                                                        framework='pt', device=0)
        all_hidden_states_with_jailbreak_harmless = []
        for idx, query_harmless in enumerate(all_queries_harmless):
            tmp_hidden_states = hidden_states_with_jailbreak.get_tensor(f'sample.{idx}_layer.{num_layers-1}')[-1]
            all_hidden_states_with_jailbreak_harmless.append(tmp_hidden_states)

        hidden_states_with_jailbreak = safe_open(f'hidden_states_with_jailbreak-v2/{model_name}_{dataset_1}.safetensors',
                                                framework='pt', device=0)
        all_hidden_states_with_jailbreak_1 = []
        for idx, query in enumerate(all_queries_1):
            tmp_hidden_states = hidden_states_with_jailbreak.get_tensor(f'sample.{idx}_layer.{num_layers-1}')[-1]
            all_hidden_states_with_jailbreak_1.append(tmp_hidden_states)

        hidden_states_with_jailbreak = safe_open(f'hidden_states_with_jailbreak-v2/{model_name}_{dataset_2}.safetensors',
                                                framework='pt', device=0)
        all_hidden_states_with_jailbreak_2 = []
        for idx, query in enumerate(all_queries_2):
            tmp_hidden_states = hidden_states_with_jailbreak.get_tensor(f'sample.{idx}_layer.{num_layers-1}')[-1]
            all_hidden_states_with_jailbreak_2.append(tmp_hidden_states)

        all_hidden_states_with_jailbreak_1 = torch.stack(all_hidden_states_with_jailbreak_1)
        all_hidden_states_with_jailbreak_2 = torch.stack(all_hidden_states_with_jailbreak_2)
        all_hidden_states_with_jailbreak_harmless = torch.stack(all_hidden_states_with_jailbreak_harmless)

        indices_with_jailbreak_1, other_indices_with_jailbreak_1 = get_following_indices(
            model_name, dataset_1, config=args.config, use_jailbreak_prompt=True, use_harmless=False)
        indices_with_jailbreak_2, other_indices_with_jailbreak_2 = get_following_indices(
            model_name, dataset_2, config=args.config, use_jailbreak_prompt=True, use_harmless=False)
        indices_with_jailbreak_harmless, other_indices_with_jailbreak_harmless = get_following_indices(
            model_name, dataset_harmless, config=args.config, use_jailbreak_prompt=True, use_harmless=True)

        with safe_open(f'./estimations/{model_name}_all/transform.safetensors', framework='pt') as f:
            mean = f.get_tensor('mean').float().to('cuda')
            V = f.get_tensor('V').float().to('cuda')

        hidden_states = torch.cat([
            all_hidden_states_harmless,
            all_hidden_states_with_jailbreak_harmless,
            all_hidden_states_1,
            all_hidden_states_with_jailbreak_1,
            all_hidden_states_2,
            all_hidden_states_with_jailbreak_2,
        ], dim=0).float()

        ax = fig.add_subplot(nrows, ncols, mdx + 1)
        ax.set_title(model_name)
        ax.set_aspect(1)

        # harmless
        points = torch.matmul(all_hidden_states_harmless - mean, V)[:, 0:].cpu().numpy()
        ax.scatter(points[other_indices_harmless, 0], points[other_indices_harmless, 1],
                    marker='o', alpha=0.38,
                    color=colors['held-out'])
        ax.scatter(points[indices_harmless, 0], points[indices_harmless, 1],
                    marker='o', alpha=0.39,
                    color=colors['held-out'], label='held-out')

        points = torch.matmul(all_hidden_states_with_jailbreak_harmless - mean, V)[:, 0:].cpu().numpy()
        ax.scatter(points[other_indices_with_jailbreak_harmless, 0], points[other_indices_with_jailbreak_harmless, 1],
                    marker='o', alpha=0.38,
                    color=colors['held-out + jailbreak'])
        ax.scatter(points[indices_with_jailbreak_harmless, 0], points[indices_with_jailbreak_harmless, 1],
                    marker='o', alpha=0.39,
                    color=colors['held-out + jailbreak'], label='held-out + jailbreak-v2')

        # advbench
        points = torch.matmul(all_hidden_states_1 - mean, V)[:, 0:].cpu().numpy()
        ax.scatter(points[other_indices_1, 0], points[other_indices_1, 1],
                    marker='x', alpha=0.42,
                    color=colors['advbench'])
        ax.scatter(points[indices_1, 0], points[indices_1, 1],
                    marker='x', alpha=0.41,
                    color=colors['advbench'], label='advbench')

        points = torch.matmul(all_hidden_states_with_jailbreak_1 - mean, V)[:, 0:].cpu().numpy()
        ax.scatter(points[other_indices_with_jailbreak_1, 0], points[other_indices_with_jailbreak_1, 1],
                    marker='x', alpha=0.42,
                    color=colors['advbench + jailbreak'])
        ax.scatter(points[indices_with_jailbreak_1, 0], points[indices_with_jailbreak_1, 1],
                    marker='x', alpha=0.41,
                    color=colors['advbench + jailbreak'], label='advbench + jailbreak-v2')

        # malicious
        points = torch.matmul(all_hidden_states_2 - mean, V)[:, 0:].cpu().numpy()
        ax.scatter(points[other_indices_2, 0], points[other_indices_2, 1],
                    marker='x', alpha=0.42,
                    color=colors['malicious'])
        ax.scatter(points[indices_2, 0], points[indices_2, 1],
                    marker='x', alpha=0.41,
                    color=colors['malicious'], label='malicious')

        points = torch.matmul(all_hidden_states_with_jailbreak_2 - mean, V)[:, 0:].cpu().numpy()
        ax.scatter(points[other_indices_with_jailbreak_2, 0], points[other_indices_with_jailbreak_2, 1],
                    marker='x', alpha=0.42,
                    color=colors['malicious + jailbreak'])
        ax.scatter(points[indices_with_jailbreak_2, 0], points[indices_with_jailbreak_2, 1],
                    marker='x', alpha=0.41,
                    color=colors['malicious + jailbreak'], label='malicious + jailbreak-v2')


        xlim = ax.get_xlim()
        ylim = ax.get_ylim()

        if (xlim[1] - xlim[0]) * 0.8 > (ylim[1] - ylim[0]):
            delta = (xlim[1] - xlim[0]) * 0.8 - (ylim[1] - ylim[0])
            ylim = (ylim[0] - delta / 2, ylim[1] + delta / 2)
        else:
            delta = (ylim[1] - ylim[0]) / 0.8 - (xlim[1] - xlim[0])
            xlim = (xlim[0] - delta / 2, xlim[1] + delta / 2)

        with safe_open(f'estimations/{model_name}_all/harmfulness.safetensors', framework='pt') as f:
            weight = torch.mean(f.get_tensor('weight'), dim=0).squeeze(0).tolist()
            bias = torch.mean(f.get_tensor('bias'), dim=0).squeeze(0).tolist()
        boundary_points = calculate_boundary(xlim, ylim, weight, bias)
        logging.info(f"harmfulness boundary: {boundary_points}")
        ax.plot([boundary_points[0][0], boundary_points[1][0]],
                [boundary_points[0][1], boundary_points[1][1]],
                color='black', alpha=1, linewidth=3, linestyle='-.')

        ax.set_xlim(xlim)
        ax.set_ylim(ylim)

        if model_name in ['Llama-2-7b-chat-hf', 'vicuna-7b-v1.5', 'CodeLlama-7b-Instruct-hf', 'Mistral-7B-Instruct-v0.2']:
            ax.invert_xaxis()

        ax.legend(fontsize=9)

    plt.tight_layout()
    plt.savefig(f"{args.output_path}/{fname}_{args.config}.pdf")

    logging_cuda_memory_usage()
    torch.cuda.empty_cache()
    gc.collect()


if __name__ == "__main__":
    main()
