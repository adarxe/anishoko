# AniShoko

Leia em [English](README.md).

Scrobbling automático e resiliente do AniList para o Jellyfin, utilizando o Shoko Server como ponte. 

Você assiste a um episódio no Jellyfin e sua lista no AniList é atualizada silenciosamente em segundo plano. Sem cliques extras, sem atrasos.

## Por que eu fiz isso?

Minha biblioteca de animes reside no Jellyfin, com o Shoko cuidando da rigorosa organização de metadados locais, enquanto eu acompanho meu progresso global pelo AniList. O gargalo estava na sincronização: toda vez que eu terminava um episódio, precisava abrir o site e atualizar o contador manualmente. 

Embora existam plugins genéricos para isso, nenhum suportava casos complexos da forma correta. Filmes, OVAs e especiais dentro de franquias massivas (como *Monogatari*, *Fate* ou *Gundam*) quase nunca possuem paridade direta de títulos entre o arquivo local e o banco de dados do AniList. Plugins normais falham silenciosamente ou marcam o anime errado.

Então, projetei meu próprio pipeline de resolução de identidades. Construído sob os princípios da **Clean Architecture**, o AniShoko opera como um daemon altamente tolerante a falhas, capaz de sobreviver a instabilidades de rede e desambiguar obras complexas com precisão cirúrgica.

## Como funciona (O Pipeline de 5 Camadas)

O Jellyfin dispara um webhook (Notificação do tipo `PlaybackStop`) quando a reprodução termina. O AniShoko recebe esse evento através de um servidor HTTP multithread e processa a resolução do ID do AniList através de um pipeline inteligente, partindo do método mais rápido (banco de dados local) para o mais complexo:

| Camada | Método de Resolução | Desempenho e Comportamento |
| :--- | :--- | :--- |
| **L1** | Cache Direto (`series_mapping`) | **Instantâneo (0ms).** Match exato por ID do Shoko e episódio via SQLite. Nenhuma requisição de rede necessária. |
| **L1.5** | Espelho Local (`anilist_mirror`) | **Instantâneo (0ms).** Busca relacional por título na sua própria lista do AniList pré-sincronizada localmente. |
| **L2** | Bridge API (Shoko Server) | **Rápido.** Mapeamento numérico via Shoko API, convertendo o GUID da série para o AniList ID oficial. |
| **L3** | SmartResolver (GraphQL) | **Moderado.** Busca direta no motor do AniList usando higienização de strings caso as camadas anteriores falhem. |
| **L3.5** | Desambiguação Relacional (Fuzzy + BFS) | **Complexo.** Ativado automaticamente para Filmes e OVAs. Percorre a árvore de franquia e isola a obra correta. |

### O Motor de Desambiguação (Capa 3.5)
A joia da coroa deste sistema. Quando a API relata que o formato é um Filme ou OVA, uma busca simples por título geralmente cai na série principal. Para corrigir isso, o AniShoko constrói uma árvore da franquia via **Busca em Largura (BFS)**, isola todos os nós em um cache local e utiliza o algoritmo de distância de Levenshtein (`RapidFuzz`) para comparar o nome composto do Jellyfin (`SeriesName` + `Name`) contra cada obra do universo. A obra com a maior pontuação recebe o scrobble. 

### Tolerância a Falhas e Idempotência
* **Network Resiliency:** Todos os clientes HTTP (`requests.Session`) possuem connection pooling e adaptadores de repetição (backoff exponencial). Quedas temporárias no AniList ou Shoko são absorvidas transparentemente.
* **Fila Offline:** Se a internet cair completamente, os scrobbles pendentes são salvos no SQLite. Um worker assíncrono (Conserje) trabalha em segundo plano para tentar novamente mais tarde.
* **Idempotência:** Requisições repetidas ou episódios já assistidos são interceptados localmente. O sistema nunca gasta sua cota de requisições do AniList para atualizar algo que já foi concluído.

## Requisitos

* Python 3.9 ou superior
* Jellyfin (com o plugin nativo de Webhooks)
* Shoko Server
* Conta no AniList

Dependências Python:
* `requests`
* `urllib3`
* `rapidfuzz`
* `python-dotenv`

*(Nota: O SQLite3 é utilizado como motor de banco de dados nativo pela sua alta performance em concorrência com o modo WAL ativado, não exigindo instalações externas).*

## Instalação e Execução

Clone o repositório e instale as dependências:

```bash
git clone [https://github.com/adarxe/anishoko.git](https://github.com/adarxe/anishoko.git)
cd anishoko
pip install -r requirements.txt

Crie um arquivo .env na raiz do projeto:
ANILIST_TOKEN=seu_token_do_anilist_aqui
SHOKO_URL=http://localhost:8111
SHOKO_API_KEY=sua_chave_do_shoko_aqui
PORT=5000

(Dica: Para obter o token do AniList, crie um client em Settings > Developer e utilize o fluxo OAuth. A API Key do Shoko encontra-se nas configurações do seu servidor).
Inicie o daemon:
python3 main.py

O primeiro boot inicializará o banco de dados SQLite e acionará o Worker de Sincronização, que fará o download nativo de toda a sua lista do AniList para o espelho local (anilist_mirror).
(Se estiver executando no Termux/Android, lembre-se de rodar termux-wake-lock antes de iniciar o processo para que o Android não suspenda o daemon em segundo plano).
Configuração no Jellyfin
 * Instale o plugin Webhook.
 * Adicione um Generic Destination apontando para: http://<seu-ip-local>:5000
 * Habilite apenas o evento Playback Stop.
 * Garanta que o payload inclua os IDs dos provedores e o Item Name. O SeriesId do Shoko é essencial para as Camadas 1 e 2 funcionarem com precisão absoluta.
Arquitetura de Software
O projeto adota a Clean Architecture para manter o código testável, isolado e expansível:
| Diretório | Responsabilidade |
|---|---|
| api/ | Servidor HTTP Multithread e controladores de eventos (Webhooks). |
| clients/ | Gerenciamento de sessões, cabeçalhos e consultas GraphQL/REST (AniList, Shoko). |
| database/ | Conexão SQLite (WAL) e camada de repositório (Querying e Caching). |
| services/ | Regras de negócio essenciais (Pipeline de Resolução, Sincronização Diária, Fila Offline). |
| main.py | Ponto de entrada do sistema. Orquestra a inicialização das threads em segundo plano. |
Metas e Próximos Passos (Roadmap)
A base do sistema já é robusta e resolve o problema principal, mas o desenvolvimento continua:
 * [x] Migrar para um servidor HTTP multithread para evitar bloqueios em picos de webhooks.
 * [x] Tratar erros HTTP 429/500 com estratégias de retry e backoff exponencial.
 * [x] Implementar cache relacional nativo para evitar abusos na API do AniList em buscas BFS.
 * [ ] Implementar integração com Pandas e Machine Learning para análise avançada de hábitos de visualização com base nos dados do SQLite.
 * [ ] Adicionar um segredo compartilhado (Authentication Token) no endpoint do webhook.
 * [ ] Disponibilizar um Dockerfile e unidade systemd para facilitar implantações em NAS e servidores caseiros.
Uma observação sobre o desenvolvimento
Toda a arquitetura, refatoração de concorrência e testes deste projeto foram construídos inteiramente em um ambiente mobile (Termux no Android). Sem IDE pesada, apenas um editor de terminal, conexões SSH intermitentes e paciência. O código prova que aplicações assíncronas e resilientes de nível de produção podem ser orquestradas a partir de ambientes extremamente restritos.
Licença
Este projeto é de código aberto. Sinta-se à vontade para clonar, modificar ou abrir uma issue caso tenha ideias para melhorar a integração.


