# Диаграммы ЛР1

Диаграммы ролей и коммуникаций включены также в [отчёт](REPORT_LR1.md).
Этот файл содержит их вместе с дополнительным автоматом состояний робота.

## Роли

```mermaid
flowchart TD
    subgraph Agents["Агенты системы"]
        D["Диспетчер D"] -->|"выполняет роль"| C["Координатор"]
        R["Роботы R1…Rn"] -->|"выполняют роль"| E["Исполнитель"]
    end
    C -->|"распределяет и учитывает"| Q["Задания"]
    E -->|"перевозит"| Q
    E -->|"использует"| S["Зарядная станция"]
```

Зарядная станция — общий инструмент среды, не агент. Шина и планировщик также не имеют агентных ролей.

## Направленный граф коммуникаций

```mermaid
flowchart TD
    D["Диспетчер D"] -->|"ASSIGN"| R1["Робот R1"]
    D -->|"ASSIGN"| R2["Робот R2"]
    D -->|"ASSIGN"| R3["Робот R3"]
    R1 -->|"STATUS, ACCEPT, REJECT, COMPLETE"| D
    R2 -->|"STATUS, ACCEPT, REJECT, COMPLETE"| D
    R3 -->|"STATUS, ACCEPT, REJECT, COMPLETE"| D
```

Для n роботов дуги D → Rᵢ и Rᵢ → D повторяются для каждого i.
Все агентные сообщения проходят через одну логическую шину с задержкой в один такт.
Прямых сообщений робот → робот в этой версии нет.

## Автомат состояний робота

```mermaid
stateDiagram-v2
    direction TB
    [*] --> idle
    idle --> to_pickup: ASSIGN принят
    to_pickup --> loading: точка погрузки
    loading --> to_dropoff: груз загружен
    to_dropoff --> unloading: точка доставки
    unloading --> returning: груз выгружен
    returning --> waiting_charge: база и неполная батарея
    returning --> idle: база и полная батарея
    waiting_charge --> charging: получено место
    charging --> idle: батарея полна
```

Отказ от назначения сохраняет текущее состояние. В `waiting_charge` робот повторяет
запрос места; в `charging` пополняет энергию до полного заряда. При возвращении
отправляет `COMPLETE`, при смене состояния — `STATUS`.

## Жизненный цикл задания

```mermaid
stateDiagram-v2
    direction TB
    [*] --> pending
    pending --> unassignable: нет допустимых роботов
    pending --> offered: ASSIGN отправлен
    offered --> active: ACCEPT
    offered --> pending: REJECT
    active --> completed: COMPLETE
    completed --> [*]
    unassignable --> [*]
```

Из `pending` переходы разрешены только после момента поступления.
