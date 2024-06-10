import sys, os, time
import numpy as np
from datetime import datetime
from joblib import Parallel, delayed

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(parent_dir)

from sklearn.preprocessing import LabelEncoder
from sklearn.model_selection import train_test_split

from estimators.statistical_descriptor import Nagler_WS
# from plot.figure_roc import ROC_plot
from utils.dataset_management import parse_pipeline
from utils.dataset_load import shuffle_data, DatasetLoader
from utils.fold_management import FoldManagement
from utils.label_management import LabelManagement
from utils.figures import plot_boxplots, plot_roc_curves
from utils.files_management import (
    load_yaml,
    dump_pkl,
    init_logger,
    report_prediction,
    report_metric_from_log,
    set_folder,
    logger_dataset,
    logger_fold
)

def fit_predict_fold(pipeline, X_train_k, y_train_k, X_test_k, y_test_k, log_model, label_encoder, kfold, pipeline_name):
    pipeline_id = f"{pipeline_name}_kfold_{kfold}"
    try:
        start_time = time.time()
        pipeline.fit(X_train_k, y_train_k)
        training_time = time.time() - start_time
        log_model.info(f"Training time : {training_time:.2f} seconds")

        start_time = time.time()
        y_prob = pipeline.predict_proba(X_test_k)
        prediction_time = time.time() - start_time
        log_model.info(f"Prediction time for : {prediction_time:.2f} seconds")

        log_model, fold_metric = report_prediction(log_model, y_test_k, y_prob, label_encoder, kfold)
        return fold_metric, y_prob, y_test_k
    except Exception as e:
        log_model.error(f"Pipeline {pipeline_id} failed")
        log_model.error(e)
        return None, None, None

def predict_dataset(
    x,
    targets,
    fold_groups,
    output_dir,
    pipeline_params,
    label_encoder,
    log_results,
    save=True
):
    y_est_save, metrics = {}, {}

    for count, pipeline_name in enumerate(pipeline_params["pipeline_names"]):
        save_dir = os.path.join(output_dir, f"models/{pipeline_name}/")
        log_model, _ = init_logger(save_dir, f"{pipeline_name}_results")

        log_model.info(f"================== Fitting model {pipeline_name} ==================")

        y_est_save[pipeline_name] = {"y_true": [], "y_est": []}
        fold_metrics = []

        def fit_predict_fold_wrap(fold, train_index, test_index):
            X_train_k, y_train_k = x[train_index], targets[train_index]
            X_test_k, y_test_k = x[test_index], targets[test_index]

            return fit_predict_fold(
                parse_pipeline(pipeline_params, count),
                X_train_k, y_train_k,
                X_test_k, y_test_k,
                log_model,
                label_encoder,
                fold,
                pipeline_name
            )

        results = Parallel(n_jobs=8)(
            delayed(fit_predict_fold_wrap)(kfold, train_index, test_index)
            for kfold, (train_index, test_index) in enumerate(fold_groups)
        )

        for (fold_metric, y_prob, y_test_k) in (results):
            if fold_metric is not None:
                fold_metrics.append(fold_metric)
                y_est_save[pipeline_name]["y_est"].extend(y_prob)
                y_est_save[pipeline_name]["y_true"].extend(y_test_k)

        if save:
            dump_pkl(fold_metrics, os.path.join(save_dir, "metrics.pkl"))
        metrics[pipeline_name] = fold_metrics

    results_dir = os.path.join(output_dir, "results/plots/")
    plot_boxplots(metrics, save_dir=results_dir)
    plot_roc_curves(metrics, save_dir=results_dir)
    log_results = report_metric_from_log(log_results, metrics)

    return y_est_save



if __name__ == "__main__":
    """
    Main entry point for the script. Loads parameters, sets up logging, 
    and runs the prediction process.
    """
    
    param_path = "pipeline/parameter/config_pipeline.yml"
    pipeline_params = load_yaml(param_path)

    try:
        data_path = pipeline_params["data_path"]
        out_dir = pipeline_params["out_dir"]
        fold_method = pipeline_params["fold_method"]
        seed = pipeline_params["seed"]
        labeling_method = pipeline_params["labeling_method"]
        resampling_method = pipeline_params["resampling_method"]
        orbit = pipeline_params["orbit"]
        request = pipeline_params["request"]
        shuffle_data = pipeline_params["shuffle_data"]
        channel_transformation = pipeline_params["channel_transformation"]
        BANDS_MAX = pipeline_params["BANDS_MAX"]

    except KeyError as e:
        print("KeyError: %s undefined" % e)

    out_dir = set_folder(out_dir, pipeline_params)
    log_dataset, _ = init_logger(out_dir, "dataset_info")
    log_results, _ = init_logger(out_dir + "results", "results")
    
    start_line = 0

    dataset_loader = DatasetLoader(
        data_path,
        shuffle=shuffle_data,
        descrp=[
            "date",
            "massif",
            "acquisition",
            "elevation",
            "slope",
            "orientation",
            "tmin",
            "hsnow",
            "tel"
        ],
        print_info=True
    )

    x, y = dataset_loader.request_data(request)

    labels_manager = LabelManagement(method=labeling_method)

    targets = labels_manager.transform(y)
    label_encoder = labels_manager.get_encoder()
    
    fold_manager = FoldManagement(targets=targets,
                                  method=fold_method,
                                  resampling_method=resampling_method, 
                                  shuffle=shuffle_data, 
                                  random_state=seed,
                                  train_aprox_size=0.8)
    
    fold_groups = fold_manager.split(x, y)

    log_dataset = logger_dataset(log_dataset, x, y, targets)
    log_dataset = logger_fold(log_dataset, fold_groups, targets, y)

    y_est_save = predict_dataset(x=x,
                                 targets=targets,
                                 fold_groups=fold_groups,
                                 output_dir=out_dir,
                                 pipeline_params=pipeline_params,
                                 label_encoder=label_encoder,
                                 log_results=log_results,
                                 save=True)
    
    print("================== End of the study ==================")