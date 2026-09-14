from typing import Annotated

from fastapi import Depends

from app.clients.pravo_ebpi import PravoEbpiClient
from app.clients.publication_pravo import PublicationPravoClient
from app.clients.rag import RagClient
from app.core.settings import get_settings
from app.dependencies.http_client import HTTPClientDep


def get_publication_pravo_client(httpx_client: HTTPClientDep) -> PublicationPravoClient:
    """Фабрика клиента publication.pravo.gov.ru."""

    settings = get_settings()
    return PublicationPravoClient(
        httpx_client=httpx_client,
        settings=settings.publication_pravo,
    )


PublicationPravoClientDep = Annotated[
    PublicationPravoClient,
    Depends(get_publication_pravo_client),
]


def get_pravo_ebpi_client(httpx_client: HTTPClientDep) -> PravoEbpiClient:
    """Фабрика клиента банка консолидированных редакций."""

    settings = get_settings()
    return PravoEbpiClient(
        httpx_client=httpx_client,
        settings=settings.pravo_ebpi,
    )


PravoEbpiClientDep = Annotated[PravoEbpiClient, Depends(get_pravo_ebpi_client)]


def get_rag_client(httpx_client: HTTPClientDep) -> RagClient:
    """Фабрика клиента RAG Service."""

    settings = get_settings()
    return RagClient(
        httpx_client=httpx_client,
        settings=settings.rag,
    )


RagClientDep = Annotated[RagClient, Depends(get_rag_client)]
