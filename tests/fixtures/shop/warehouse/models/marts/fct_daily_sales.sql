with o as (select * from {{ ref('stg_orders') }})
select day, sum(amount) as revenue from o group by day
