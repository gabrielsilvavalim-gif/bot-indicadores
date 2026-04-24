TypeError: This app has encountered an error. The original error message is redacted to prevent data leaks. Full error details have been recorded in the logs (if you're on Streamlit Cloud, click on 'Manage app' in the lower right of your app).
Traceback:
File "/mount/src/bot-indicadores/bot_indicadores.py", line 1224, in <module>
    st.dataframe(
    ~~~~~~~~~~~~^
        df_mom_tela.style
        ^^^^^^^^^^^^^^^^^
    ...<14 lines>...
        hide_index=True,
        ^^^^^^^^^^^^^^^^
    )
    ^
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/runtime/metrics_util.py", line 563, in wrapped_func
    result = non_optional_func(*args, **kwargs)
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/elements/arrow.py", line 928, in dataframe
    marshall_styler(proto.arrow_data, data, default_uuid)
    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/streamlit/elements/lib/pandas_styler_utils.py", line 67, in marshall_styler
    pandas_styles = styler._translate(False, False)  # type: ignore
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 361, in _translate
    body: list = self._translate_body(idx_lengths, max_rows, max_cols)
                 ~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 665, in _translate_body
    body_row = self._generate_body_row(
        (r, row_tup, rlabels), max_cols, idx_lengths
    )
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 859, in _generate_body_row
    display_value=self._display_funcs[(r, c)](value),
                  ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^
File "/home/adminuser/venv/lib/python3.14/site-packages/pandas/io/formats/style_render.py", line 1984, in <lambda>
    func_0 = lambda x: formatter.format(x)
                       ~~~~~~~~~~~~~~~~^^^
