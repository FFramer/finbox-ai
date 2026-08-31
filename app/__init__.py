"""Pacote da aplicação Finbox AI.

O truststore faz o Python validar TLS pelo repositorio de certificados do
sistema operacional, em vez do bundle estatico do certifi. Sem isso, qualquer
antivirus ou proxy corporativo que inspeciona HTTPS (aqui, o Avast Web/Mail
Shield) reassina os certificados com uma raiz própria que existe no store do
SO mas não no certifi, e toda chamada httpx falha com CERTIFICATE_VERIFY_FAILED.

Precisa rodar antes de qualquer contexto SSL ser criado, por isso fica aqui.
"""

import truststore

truststore.inject_into_ssl()
