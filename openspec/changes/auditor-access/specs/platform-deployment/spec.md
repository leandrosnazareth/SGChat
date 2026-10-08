# Spec Delta

## MODIFIED Requirements

### Requirement: Exposição mínima de portas
Somente as interfaces de uso humano (portal de chat, interface do Langfuse e portal de auditoria) SHALL ser publicadas no host. Bancos, cache, armazenamento de objetos, Presidio e IA Local MUST NOT ser publicados no host.

#### Scenario: Portas publicadas
- **WHEN** as portas publicadas pela stack são inspecionadas
- **THEN** apenas o portal de chat, a interface do Langfuse, o portal de auditoria e, opcionalmente, o gateway em `127.0.0.1` aparecem publicados
