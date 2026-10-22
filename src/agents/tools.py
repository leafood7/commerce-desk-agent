import math
import re

import numexpr
from langchain_chroma import Chroma
from langchain_core.tools import BaseTool, tool
from langchain_openai import OpenAIEmbeddings


def calculator_func(expression: str) -> str:
    """Calculates a math expression using numexpr.

    Useful for when you need to answer questions about math using numexpr.
    This tool is only for math questions and nothing else. Only input
    math expressions.

    Args:
        expression (str): A valid numexpr formatted math expression.

    Returns:
        str: The result of the math expression.
    """

    try:
        local_dict = {"pi": math.pi, "e": math.e}
        output = str(
            numexpr.evaluate(
                expression.strip(),
                global_dict={},  # 限制对全局变量的访问
                local_dict=local_dict,  # 添加常用数学函数
            )
        )
        return re.sub(r"^\[|\]$", "", output)
    except Exception as e:
        raise ValueError(
            f'calculator("{expression}") raised error: {e}.'
            " Please try again with a valid numerical expression"
        )


calculator: BaseTool = tool(calculator_func)
calculator.name = "Calculator"


# 整理检索到的文档
def format_contexts(docs):
    return "\n\n".join(doc.page_content for doc in docs)


def load_chroma_db():
    # 为项目介绍知识库创建向量转换函数
    try:
        embeddings = OpenAIEmbeddings()
    except Exception as e:
        raise RuntimeError(
            "Failed to initialize OpenAIEmbeddings. Ensure the OpenAI API key is set."
        ) from e

    # 加载已保存的向量数据库
    chroma_db = Chroma(persist_directory="./chroma_db", embedding_function=embeddings)
    retriever = chroma_db.as_retriever(search_kwargs={"k": 5})
    return retriever


def database_search_func(query: str) -> str:
    """Searches chroma_db for information in the company's handbook."""
    # 获取 Chroma 检索器
    retriever = load_chroma_db()

    # 从数据库中检索相关文档
    documents = retriever.invoke(query)

    # 将文档整理成字符串
    context_str = format_contexts(documents)

    return context_str


database_search: BaseTool = tool(database_search_func)
database_search.name = "Database_Search"  # 请根据知识库的用途修改名称
