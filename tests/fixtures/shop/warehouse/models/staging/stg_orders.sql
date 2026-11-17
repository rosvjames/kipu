select id, amount, day from {{ source('raw', 'orders') }}
