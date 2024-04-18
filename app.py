from io import BytesIO
import json

import matplotlib.pyplot as plt
import streamlit as st
import plotly.express as px
from plotly.subplots import make_subplots

import torch
from torchvision.transforms.functional import to_tensor, to_pil_image

from torchcam import methods
from torchcam.utils import overlay_mask

from PIL import Image

from src.model_library import XRVModelLibrary, AbstractModelLibrary
from src.feedback_utils import Feedback
from src.db_interface import MetadataStore, get_image_from_azure, setup_container_client, write_data_to_azure_blob


# All supported CAM
# CAM_METHODS = ["CAM", "GradCAM", "GradCAMpp", "SmoothGradCAMpp", "ScoreCAM", "SSCAM", "ISCAM", "XGradCAM", "LayerCAM"]

# Supported CAMs for multiple target layers
CAM_METHODS = [
    "GradCAM",
    "GradCAMpp",
    "SmoothGradCAMpp",
    "XGradCAM",
    "LayerCAM",
    "ScoreCAM",
    "SSCAM",
    "ISCAM",
]
MODEL_SOURCES = ["XRV"]
NUM_RESULTS = 3
N_IMAGES = 10
RESULTS_PER_ROW = 3


def main():
    # Wide mode
    st.set_page_config(page_title="Chest X-ray Investigation", page_icon="🚑", layout="wide", initial_sidebar_state="collapsed")

    # Designing the interface
    # st.title("Chest X-ray Investigation")

    # Sidebar
    st.sidebar.title("How-To")

    st.sidebar.write("This is a tool to evaluate AI predictions on chest X-ray images. Lorem ipsum dolor sit amet, consectetur adipiscing elit. Nulla nec purus feugiat, molestie ipsum et, consequat nibh. Ut sit amet odio eu est aliquet euismod a ante")

    # Enter access key
    account_key = st.sidebar.text_input("account_key", value=None)

    if "current_index" not in st.session_state:
        st.session_state.current_index = 0

    if "images" not in st.session_state:

        metadata = MetadataStore()
        st.session_state["feedback"] = Feedback()
        

        if account_key is not None:
            container_client = setup_container_client(account_key)

            metadata.read_from_azure(container_client)

            # TODO: Add possibility to filter by label or not (maybe through checkboxes or a multiselectbox)
            # We need metadataStore for that, so load beforehand
            # Also we need to reload image_filenames when we change the filter
            # Is there a on_change event for selectbox?

            # filter_label = st.sidebar.selectbox(
            #     "Filter Label", metadata.get_unique_labels()
            # )

            # img = None
            # if filter_label is not None:
            #     image_filenames = metadata.get_random_image_filenames(
            #         N_IMAGES, filter_label
            #     )

            image_filenames = metadata.get_random_image_filenames(N_IMAGES)

            images = []
            for image_filename in image_filenames:
                img = {
                    "filename": image_filename,
                    "label": metadata.get_full_label(image_filename),
                }
                images.append(img)

            st.session_state["images"] = images
            st.session_state["container_client"] = container_client
            st.session_state.num_result = 0

    # model_source = st.sidebar.selectbox(
    #     "Classification model source",
    #     MODEL_SOURCES,
    #     help="Supported models from Torchxrayvision",
    # )

    # model_lib = AbstractModelLibrary()
    # if model_source is not None:
    #     if model_source == "XRV":
    #         model_lib = XRVModelLibrary()

    # model_choice = st.sidebar.selectbox(
    #     "Model choice",
    #     model_lib.CHOICES,
    # )

    model_source = "XRV"
    model_lib = XRVModelLibrary()
    model_choice = "densenet121-res224-all"

    model = None
    if model_source is not None:
        with st.spinner("Loading model..."):
            model = model_lib.get_model(model_choice)

    for p in model.parameters():
        p.requires_grad_(False)

    # CAM selection
    # cam_method = st.sidebar.selectbox(
    #     "CAM method",
    #     CAM_METHODS,
    #     help="The way your class activation map will be computed",
    # )

    # For newline
    st.sidebar.write("\n")

    # st.sidebar.multiselect(
    #     "CAM choices",
    #     CAM_METHODS,
    #     help="The way your class activation map will be computed",
    #     max_selections=3,
    #     key="cam_choices",
    #     default=["GradCAM", "GradCAMpp"]
    # )

    # cam_choices = st.session_state["cam_choices"]

    cam_choices = CAM_METHODS

    if "images" in st.session_state and st.session_state.current_index < N_IMAGES:

        # if st.session_state.num_result == NUM_RESULTS:
        image = st.session_state.images[st.session_state.current_index]

        diagnose(image, model, cam_choices, model_lib)
    else:
        st.write("No more images to diagnose")


def diagnose(
    img: dict, model: torch.nn.Module, cam_choices: list, model_lib: XRVModelLibrary
):
    # input_col = st.columns(1)

    input_col, result_col = st.columns([0.5, 0.5], gap="medium")

    blob_data = get_image_from_azure(st.session_state["container_client"], img["filename"])
    img_data = Image.open(BytesIO(blob_data.read()), mode="r").convert("RGB")

    img_tensor = to_tensor(img_data)

    with input_col:
        with st.container(border=True):
            fig = plt.figure(figsize=(6, 6))
            fig.tight_layout()
            ax1 = fig.add_axes([0.1, 0.1, 0.8, 0.8])
            ax1.axis("off")
            ax1.imshow(to_pil_image(img_tensor))
            st.header("Input X-ray image")
            st.pyplot(fig)

            st.write(f"Store label: {img['label']}")

    if model is None:
        st.sidebar.error("Please select a classification model")
    # elif cam_method is None:
    #     st.sidebar.error("Please select CAM method.")
    else:
        with result_col:
            with st.spinner("Analyzing..."):
                # result_cols = [None for i in range(NUM_RESULTS)]
                # feedback_cols = [None for i in range(NUM_RESULTS)]

                # Preprocess image
                transformed_img, rescaled_img = model_lib.preprocess(img_tensor)

                rescaled_img.requires_grad_(True)

                if torch.cuda.is_available():
                    model = model.cuda()
                    rescaled_img = rescaled_img.cuda()

                # Show results
                with st.form("form"):

                    # result_tabs = st.tabs([f"Result {i+1}" for i in range(NUM_RESULTS)])

                    # for i in range(NUM_RESULTS):
                    #     with result_tabs[i]:
                    #         result_cols[i], feedback_cols[i] = (
                    #             st.container(),
                    #             st.container(),
                    #         )

                    result_container = st.container()
                    feedback_container = st.container()

                    fig, class_ids, out = compute_cam(cam_choices, model, model_lib, rescaled_img, img_data)

                    with result_container:
                        class_label = model_lib.LABELS[class_ids[st.session_state.num_result].item()]
                        st.header(f"Finding: {class_label} ({(st.session_state.num_result + 1)}/{NUM_RESULTS})")
                        st.plotly_chart(fig, use_container_width=True, theme="streamlit")
                        # tabs = st.tabs(cam_choices)
                        # for idx, tab in enumerate(tabs):
                        #     with tab:
                        #         st.plotly_chart(figs[idx], use_container_width=True, theme="streamlit")
                        # st.pyplot(fig2)
                        # components.html(mpld3.fig_to_html(fig2), height=500, width=500)

                    with feedback_container:

                        cols = st.columns(2)

                        with cols[0]:
                            probability = out.squeeze(0)[class_ids[st.session_state.num_result]].item() * 100
                            st.write(f"**Probability** : {probability:.2f}%")
                            st.checkbox("Confirm Finding", key=f"confirm{st.session_state.num_result}")
                            st.text_area("Comment", key=f"comment{st.session_state.num_result}")
                        
                        with cols[1]:
                            st.selectbox(
                                "Best CAM method*",
                                cam_choices,
                                index=None,
                                help="The best CAM method for this image",
                                key=f"best_cam_method{st.session_state.num_result}",
                                placeholder="Select the best CAM method",
                            )

                    if st.session_state.num_result == NUM_RESULTS - 1:
                        submit_label = "Next Patient"
                        # st.session_state.current_index += 1
                        # st.session_state.num_result = 0
                    else:
                        submit_label = "Next Result"
                        # st.session_state.num_result += 1

                    st.form_submit_button(
                        submit_label,
                        use_container_width=True,
                        type="primary",
                        # key="submit_button",
                        on_click=give_feedback,
                    )

def activate_feedback(feedback: Feedback):

    if st.session_state[f"best_cam_method{st.session_state.num_result}"] is not None:
        st.session_state["submit_button"].disabled = False

def compute_cam(cam_choices: list, model: torch.nn.Module, model_lib, rescaled_img, img_data) -> None:
    cam_extractors = []
    # Initialize CAM

    for cam_method in cam_choices:
        cam_extractor_method = methods.__dict__[cam_method](
            model,
            target_layer=model_lib.TARGET_LAYER,
            enable_hooks=False,
        )
        cam_extractors.append(cam_extractor_method)

    # fig2 = plt.figure()
    # fig2.tight_layout()
    # plt.rcParams['figure.facecolor'] = st.get_option("theme.backgroundColor")
        
    fig = make_subplots(rows=3, cols=3, subplot_titles=cam_choices, horizontal_spacing=0.05, vertical_spacing=0.05)

    for idx, cam_extractor in enumerate(cam_extractors):
        # Forward
        cam_extractor._hooks_enabled = True

        model.zero_grad()
        out = model(rescaled_img.unsqueeze(0))

        # Select the target class
        class_ids = torch.topk(out.squeeze(0), NUM_RESULTS).indices

        activation_maps = cam_extractor(
            class_idx=class_ids[st.session_state.num_result].item(), scores=out
        )

        # Fuse the CAMs if there are several
        activation_map = (
            activation_maps[0]
            if len(activation_maps) == 1
            else cam_extractor.fuse_cams(activation_maps)
        )

        cam_extractor.remove_hooks()
        cam_extractor._hooks_enabled = False

        result = overlay_mask(
            # to_pil_image(transformed_img.expand(3, -1, -1)),
            img_data,
            to_pil_image(activation_map.squeeze(0), mode="F"),
            alpha=0.7,
        )

        row = idx // RESULTS_PER_ROW
        col = idx % RESULTS_PER_ROW

        # fig.add_image(result, row=row + 1, col=col + 1)
        fig.add_trace(px.imshow(result).data[0], row=row + 1, col=col + 1)
        fig.update_xaxes(visible=False)
        fig.update_yaxes(visible=False)
        # figs.append(fig)
    
    fig.update_layout(height=500, width=800, margin=dict(l=20, r=20, t=50, b=20))

        

        # ax2 = fig2.add_axes([0.1 + col * 0.3, 0.1 + row * 0.3, 0.25, 0.25])
        # ax2.set_title(cam_choices[idx])
        # ax2.axis("off")
        # ax2.imshow(result)

    return fig, class_ids, out


def give_feedback():

    # for i in range(NUM_RESULTS):
    selection_dict = {
        "confirm": st.session_state[f"confirm{st.session_state.num_result}"],
        "comment": st.session_state[f"comment{st.session_state.num_result}"],
        "best_cam_method": st.session_state[f"best_cam_method{st.session_state.num_result}"],
    }

    image_name = st.session_state.images[st.session_state.current_index]["filename"]

    feedback_dict = {
        "result": st.session_state.num_result,
        "selection": selection_dict,
    }

    st.session_state["feedback"].insert(image_name, feedback_dict)

    if st.session_state.num_result == NUM_RESULTS - 1:
        st.session_state.current_index += 1
        st.session_state.num_result = 0
        feedback_json = json.dumps(dict(st.session_state["feedback"].get_data()), indent=4)
        write_data_to_azure_blob(
            st.session_state["container_client"],
            f"feedback/feedback_{_get_session().id}.json",
            feedback_json,
        )
    else:
        st.session_state.num_result += 1

def _get_session():
    from streamlit.runtime import get_instance
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    runtime = get_instance()
    session_id = get_script_run_ctx().session_id
    session_info = runtime._session_mgr.get_session_info(session_id)
    if session_info is None:
        raise RuntimeError("Couldn't get your Streamlit Session object.")
    return session_info.session


if __name__ == "__main__":
    main()

