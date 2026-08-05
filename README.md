# anishoko

Leia em [English](README.md).

Scrobbling automático do AniList para o Jellyfin, usando o Shoko como ponte.

Você assiste um episódio no Jellyfin. Sua lista no AniList se atualiza sozinha. É essa a ideia toda.

## Por que eu fiz isso

Minha biblioteca de anime fica no Jellyfin, com o Shoko cuidando dos metadados, e eu acompanho tudo pelo AniList. O problema estava justamente na segunda metade: toda vez que eu terminava um episódio, precisava abrir o site e aumentar o contador na mão. Eu esquecia por uma semana e depois ficava tentando lembrar se tinha parado no episódio 6 ou no 8.

Já existem plugins que fazem isso, mas nenhum funcionava bem no meu setup, principalmente com filmes e OVAs dentro de franquias grandes, onde o título do arquivo e o título no AniList quase nunca batem. Então escrevi meu próprio resolvedor.

Aviso: é um projeto pessoal. Funciona na minha máquina e resolve o meu problema. Ainda não é um produto acabado.

## Como funciona

O Jellyfin dispara um webhook quando a reprodução para. Esse script fica escutando na porta 5000 e faz o seguinte:

**1. Descarta tudo que não é anime.**
Se o payload trouxer um ID do IMDb, TVDb ou TMDb, é conteúdo ocidental e sai fora na hora.

**2. Confere se o episódio foi realmente assistido.**
Ou o Jellyfin marca como assistido até o fim, ou você passou de 85 por cento da duração. Menos que isso é ignorado, porque abrir um arquivo por dois minutos não conta como assistir.

**3. Resolve o ID do AniList em cinco camadas, da mais barata para a mais cara.**

| Camada | Método | Observação |
|---|---|---|
| L1 | Cache local `series_mapping` | Match exato por ID do Shoko mais episódio. Sem chamada de rede. |
| L1.5 | `anilist_mirror` local, busca por título | Rápido, mas comparação por texto pode ser ambígua. |
| L2 | API do Shoko para ID do MAL para ID do AniList | Mapeamento numérico, bem confiável quando o Shoko tem o dado. |
| L3 | Busca por texto no GraphQL do AniList | Último recurso. |
| L3.5 | Desambiguação por relações | Só para filmes, OVAs e especiais. |

A camada 3.5 é a parte de que mais gosto. Quando o formato é filme ou OVA, uma busca simples por título quase sempre cai na entrada errada, normalmente a série principal. Então, em vez disso, o script percorre o grafo de relações da franquia com uma BFS (limitada a 30 nós), junta todos os nós do tipo ANIME que encontrar, e pontua cada candidato contra o título recebido usando token set ratio do RapidFuzz. A maior pontuação vence. Isso corrigiu vários scrobbles errados aqui.

Tudo que é resolvido fora da camada 1 é gravado no cache local, então o próximo episódio daquela série já cai direto na L1.

**4. Grava o progresso no AniList.**
Uma mutation GraphQL define progresso e status, com algumas regras por cima:

- Filmes, especiais e entradas de episódio único são forçados para progresso 1 e marcados como COMPLETED
- Se o episódio alvo alcança ou passa o total, a entrada vira COMPLETED
- Se o alvo é menor do que o já registrado, a mutation é cancelada. Rever o episódio 3 não pode desfazer o seu progresso

**5. Aguenta ficar offline.**
Se o AniList estiver inacessível ou a resolução falhar, o evento entra numa fila no DuckDB. Um worker em segundo plano tenta de novo depois, inclusive resolvendo o ID mais tarde caso não tenha conseguido na hora.

Um segundo worker espelha sua lista do AniList no banco local no boot e a cada 24 horas. Esse espelho é o que torna as camadas 1 e 1.5 possíveis.

## Requisitos

- Python 3.9 ou mais novo
- Uma instância do Jellyfin com o plugin de Webhook
- Um Shoko Server rodando
- Uma conta no AniList

Pacotes Python:

```
requests
rapidfuzz
python-dotenv
duckdb
```

## Instalação

Clonar e instalar:

```bash
git clone https://github.com/adarxe/anishoko.git
cd anishoko
pip install requests rapidfuzz python-dotenv duckdb
```

Criar um arquivo `.env` na raiz do projeto:

```
ANILIST_TOKEN=seu_token_do_anilist
SHOKO_URL=http://localhost:8111
SHOKO_API_KEY=sua_chave_do_shoko
```

Para pegar o token do AniList, crie um client em Settings, Developer, e depois use o fluxo OAuth para obter o token. A chave da API do Shoko está nas configurações do Shoko Server.

Inicializar o banco:

```bash
python3 db_init_duckdb.py
```

Rodar:

```bash
python3 main.py
```

O servidor sobe na porta 5000. O primeiro boot também dispara um sync completo da sua lista do AniList para o espelho local, então dá um tempo para ele terminar.

## Configuração no Jellyfin

Instale o plugin de Webhook, adicione um Generic Destination e aponte para:

```
http://<host-rodando-o-anishoko>:5000
```

Habilite apenas o tipo de notificação **Playback Stop**. O script ignora o resto de qualquer forma, mas não há motivo para mandar.

Garanta que o payload inclua os provider IDs. O ID de série do Shoko é o que faz as camadas 1 e 2 funcionarem, e sem ele o script cai na comparação por texto, que é bem menos precisa.

## Arquivos

| Arquivo | O que faz |
|---|---|
| `main.py` | Servidor de webhook, resolvedor, mutations do AniList e workers em segundo plano |
| `db_manager.py` | Camada de acesso ao DuckDB, operações de cache e de fila |
| `db_init_duckdb.py` | Cria o schema do banco |
| `fetch_anilist_mirror.py` | Puxa sua lista do AniList para o espelho local |

## Limitações conhecidas

Sendo honesto sobre o que ainda não está pronto:

- O servidor HTTP atende uma requisição por vez e faz toda a resolução no mesmo fluxo. Uma resposta lenta do AniList segura o próximo webhook.
- Não há autenticação no endpoint. Qualquer um que alcance a porta 5000 consegue postar nela. Mantenha isso na rede local.
- Os limites de requisição do AniList não são tratados explicitamente. Maratonando muitos episódios rápido, dá para bater no teto.
- O limiar de 85 por cento e a porta estão fixos no código.
- As árvores de relação são baixadas de novo toda vez que a camada 3.5 roda para um título novo. Não há cache delas.
- Não há testes.

## Próximos passos

Ordem aproximada do que quero corrigir:

- Migrar para um servidor HTTP com threads e responder o webhook na hora, fazendo o trabalho em segundo plano
- Adicionar um segredo compartilhado no endpoint do webhook
- Tratar HTTP 429 do AniList com backoff adequado
- Mover limiares, porta e caminhos para o `.env`
- Adicionar `requirements.txt`, `.env.example` e um `.gitignore`
- Entregar um Dockerfile e um unit do systemd para facilitar rodar como serviço
- Cachear as árvores de relação das franquias
- Escrever testes para as camadas de resolução

## Uma observação sobre como isso foi construído

Cada linha disso foi escrita no celular. Sem PC, sem notebook, sem IDE. Só um editor de texto, um emulador de terminal e bastante paciência. Se a formatação estiver estranha em alguns pontos ou o histórico de commits estiver bagunçado, é por isso.

## Licença

Ainda não definida. Até lá, considere todos os direitos reservados. Fique à vontade para abrir uma issue se quiser usar isso para alguma coisa.

