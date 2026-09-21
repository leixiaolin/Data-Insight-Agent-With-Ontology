-- Ontology Data Agent - deterministic MySQL 8 demo data
-- Safe to rerun after 01_create_demo_schema.sql: existing demo rows are replaced.

USE ai_data_insight;

START TRANSACTION;

DELETE FROM salesorderdetail;
DELETE FROM salesorderheader;
DELETE FROM salescustomeraddress;
DELETE FROM salesproduct;
DELETE FROM salesproductcategory;
DELETE FROM salesaddress;
DELETE FROM salescustomer;

INSERT INTO salescustomer
  (CustomerID, Title, FirstName, MiddleName, LastName, Suffix,
   CompanyName, EmailAddress, Phone)
VALUES
  (1, 'Ms.', 'Alice', NULL, 'Chen', NULL, NULL, 'alice.chen@example.com', '+1-206-555-0101'),
  (2, 'Mr.', 'Brian', 'J', 'Miller', NULL, 'Northwind Bikes', 'brian.miller@northwind.example', '+1-425-555-0102'),
  (3, 'Ms.', 'Carla', NULL, 'Gomez', NULL, NULL, 'carla.gomez@example.com', '+1-512-555-0103'),
  (4, 'Dr.', 'Daniel', 'K', 'Kim', NULL, 'Contoso Cycling', 'daniel.kim@contoso.example', '+1-415-555-0104'),
  (5, 'Ms.', 'Emma', NULL, 'Dubois', NULL, NULL, 'emma.dubois@example.fr', '+33-1-55-55-0105'),
  (6, 'Mr.', 'Felix', NULL, 'Schmidt', NULL, 'Alpine Sports GmbH', 'felix@alpine.example.de', '+49-30-555-0106'),
  (7, 'Ms.', 'Grace', 'L', 'Wang', NULL, NULL, 'grace.wang@example.cn', '+86-21-5550-0107'),
  (8, 'Mr.', 'Hiro', NULL, 'Tanaka', NULL, 'Tokyo Cycle Works', 'hiro@tokyocycle.example.jp', '+81-3-5550-0108'),
  (9, 'Ms.', 'Isabella', NULL, 'Rossi', NULL, NULL, 'isabella.rossi@example.it', '+39-02-555-0109'),
  (10, 'Mr.', 'Jack', 'P', 'Wilson', 'Jr.', 'Adventure Retail', 'jack@adventure-retail.example', '+1-303-555-0110');

INSERT INTO salesaddress
  (AddressID, AddressLine1, AddressLine2, City, StateProvince, CountryRegion, PostalCode)
VALUES
  (101, '100 Pine Street', NULL, 'Seattle', 'Washington', 'United States', '98101'),
  (102, '220 Lake View', 'Suite 8', 'Bellevue', 'Washington', 'United States', '98004'),
  (103, '501 Congress Avenue', NULL, 'Austin', 'Texas', 'United States', '78701'),
  (104, '88 Market Street', NULL, 'San Francisco', 'California', 'United States', '94105'),
  (105, '12 Rue de Rivoli', NULL, 'Paris', 'Ile-de-France', 'France', '75001'),
  (106, '42 Friedrichstrasse', NULL, 'Berlin', 'Berlin', 'Germany', '10117'),
  (107, '188 Nanjing Road', 'Floor 12', 'Shanghai', 'Shanghai', 'China', '200001'),
  (108, '3-1 Marunouchi', NULL, 'Tokyo', 'Tokyo', 'Japan', '100-0005'),
  (109, '25 Via Torino', NULL, 'Milan', 'Lombardy', 'Italy', '20123'),
  (110, '1600 Blake Street', NULL, 'Denver', 'Colorado', 'United States', '80202'),
  (111, '90 Harbor Avenue', 'Warehouse B', 'Seattle', 'Washington', 'United States', '98134'),
  (112, '725 Industrial Way', NULL, 'Oakland', 'California', 'United States', '94607');

INSERT INTO salescustomeraddress (CustomerID, AddressID, AddressType)
VALUES
  (1, 101, 'Billing'), (1, 101, 'Shipping'),
  (2, 102, 'Billing'), (2, 111, 'Shipping'),
  (3, 103, 'Billing'), (3, 103, 'Shipping'),
  (4, 104, 'Billing'), (4, 112, 'Shipping'),
  (5, 105, 'Billing'), (5, 105, 'Shipping'),
  (6, 106, 'Billing'), (6, 106, 'Shipping'),
  (7, 107, 'Billing'), (7, 107, 'Shipping'),
  (8, 108, 'Billing'), (8, 108, 'Shipping'),
  (9, 109, 'Billing'), (9, 109, 'Shipping'),
  (10, 110, 'Billing'), (10, 110, 'Shipping');

INSERT INTO salesproductcategory
  (ProductCategoryID, ParentProductCategoryID, Name)
VALUES
  (1, NULL, 'Bikes'),
  (2, NULL, 'Components'),
  (3, NULL, 'Clothing'),
  (4, NULL, 'Accessories'),
  (11, 1, 'Mountain Bikes'),
  (12, 1, 'Road Bikes'),
  (21, 2, 'Brakes'),
  (22, 2, 'Wheels'),
  (31, 3, 'Jerseys'),
  (32, 3, 'Shorts'),
  (41, 4, 'Helmets'),
  (42, 4, 'Bottles and Cages');

INSERT INTO salesproduct
  (ProductID, Name, ProductNumber, Color, StandardCost, ListPrice, Size, Weight,
   ProductCategoryID)
VALUES
  (1001, 'Trail Explorer 500', 'BK-MTN-500', 'Black', 780.00, 1299.00, 'M', 13500.00, 11),
  (1002, 'Trail Explorer 700', 'BK-MTN-700', 'Red', 1120.00, 1899.00, 'L', 12800.00, 11),
  (1003, 'Summit Pro Carbon', 'BK-MTN-900', 'Blue', 2050.00, 3499.00, 'M', 9900.00, 11),
  (1004, 'Road Sprint 300', 'BK-RD-300', 'Silver', 650.00, 1099.00, '54', 9200.00, 12),
  (1005, 'Road Sprint 600', 'BK-RD-600', 'Black', 1250.00, 2199.00, '56', 8100.00, 12),
  (1006, 'Aero Elite', 'BK-RD-900', 'Red', 2400.00, 3999.00, '54', 7200.00, 12),
  (1101, 'Hydraulic Brake Set', 'CP-BR-100', 'Black', 95.00, 169.00, NULL, 780.00, 21),
  (1102, 'Alloy Wheel Set', 'CP-WH-200', 'Silver', 210.00, 379.00, '700C', 1850.00, 22),
  (1103, 'Carbon Wheel Set', 'CP-WH-500', 'Black', 620.00, 1099.00, '700C', 1380.00, 22),
  (1201, 'Classic Cycling Jersey', 'CL-JR-100', 'Blue', 28.00, 59.00, 'M', 240.00, 31),
  (1202, 'Performance Jersey', 'CL-JR-300', 'Red', 45.00, 89.00, 'L', 210.00, 31),
  (1203, 'Bib Shorts', 'CL-SH-200', 'Black', 52.00, 99.00, 'M', 260.00, 32),
  (1301, 'Sport Helmet', 'AC-HL-100', 'White', 32.00, 65.00, 'M', 310.00, 41),
  (1302, 'Aero Helmet', 'AC-HL-300', 'Black', 78.00, 149.00, 'L', 270.00, 41),
  (1303, 'Water Bottle and Cage', 'AC-BC-100', 'Blue', 8.00, 19.00, NULL, 190.00, 42);

-- Amounts are derived after details are inserted. Status 5 means shipped; status 2
-- represents approved but not yet shipped orders and intentionally has NULL ShipDate.
INSERT INTO salesorderheader
  (SalesOrderID, RevisionNumber, DueDate, ShipDate, Status, OnlineOrderFlag,
   SalesOrderNumber, PurchaseOrderNumber, AccountNumber, CustomerID,
   ShipToAddressID, BillToAddressID, ShipMethod, SubTotal, TaxAmt, Freight,
   TotalDue, OrderDate)
VALUES
  (5001, 1, '2023-01-15 00:00:00', '2023-01-08 10:00:00', 5, TRUE,  'SO-2023-0001', NULL,          'AW000001', 1, 101, 101, 'Ground', 0, 0, 0, 0, '2023-01-03'),
  (5002, 1, '2023-01-28 00:00:00', '2023-01-20 14:00:00', 5, FALSE, 'SO-2023-0002', 'PO-NW-1001',  'AW000002', 2, 111, 102, 'Freight', 0, 0, 0, 0, '2023-01-16'),
  (5003, 1, '2023-02-20 00:00:00', '2023-02-13 09:00:00', 5, TRUE,  'SO-2023-0003', NULL,          'AW000003', 3, 103, 103, 'Express', 0, 0, 0, 0, '2023-02-08'),
  (5004, 1, '2023-03-10 00:00:00', '2023-03-03 11:00:00', 5, FALSE, 'SO-2023-0004', 'PO-CC-2201',  'AW000004', 4, 112, 104, 'Freight', 0, 0, 0, 0, '2023-02-24'),
  (5005, 1, '2023-03-27 00:00:00', '2023-03-19 15:00:00', 5, TRUE,  'SO-2023-0005', NULL,          'AW000005', 5, 105, 105, 'International', 0, 0, 0, 0, '2023-03-13'),
  (5006, 1, '2023-04-18 00:00:00', '2023-04-10 10:30:00', 5, FALSE, 'SO-2023-0006', 'PO-AS-3100',  'AW000006', 6, 106, 106, 'International', 0, 0, 0, 0, '2023-04-04'),
  (5007, 1, '2023-05-22 00:00:00', '2023-05-15 13:00:00', 5, TRUE,  'SO-2023-0007', NULL,          'AW000007', 7, 107, 107, 'International', 0, 0, 0, 0, '2023-05-08'),
  (5008, 1, '2023-06-19 00:00:00', '2023-06-12 16:00:00', 5, FALSE, 'SO-2023-0008', 'PO-TC-4008',  'AW000008', 8, 108, 108, 'International', 0, 0, 0, 0, '2023-06-05'),
  (5009, 1, '2023-07-24 00:00:00', '2023-07-17 09:30:00', 5, TRUE,  'SO-2023-0009', NULL,          'AW000009', 9, 109, 109, 'International', 0, 0, 0, 0, '2023-07-10'),
  (5010, 1, '2023-08-21 00:00:00', '2023-08-14 12:00:00', 5, FALSE, 'SO-2023-0010', 'PO-AR-5100',  'AW000010', 10, 110, 110, 'Ground', 0, 0, 0, 0, '2023-08-07'),
  (5011, 1, '2023-09-18 00:00:00', '2023-09-11 10:00:00', 5, TRUE,  'SO-2023-0011', NULL,          'AW000001', 1, 101, 101, 'Ground', 0, 0, 0, 0, '2023-09-04'),
  (5012, 1, '2023-10-23 00:00:00', '2023-10-16 14:00:00', 5, FALSE, 'SO-2023-0012', 'PO-NW-1012',  'AW000002', 2, 111, 102, 'Freight', 0, 0, 0, 0, '2023-10-09'),
  (5013, 1, '2023-11-20 00:00:00', '2023-11-13 11:00:00', 5, TRUE,  'SO-2023-0013', NULL,          'AW000003', 3, 103, 103, 'Express', 0, 0, 0, 0, '2023-11-06'),
  (5014, 1, '2023-12-18 00:00:00', '2023-12-11 15:00:00', 5, FALSE, 'SO-2023-0014', 'PO-CC-2214',  'AW000004', 4, 112, 104, 'Freight', 0, 0, 0, 0, '2023-12-04'),
  (5015, 1, '2023-12-29 00:00:00', '2023-12-22 09:00:00', 5, TRUE,  'SO-2023-0015', NULL,          'AW000005', 5, 105, 105, 'International', 0, 0, 0, 0, '2023-12-15'),
  (5016, 1, '2024-01-22 00:00:00', '2024-01-15 10:00:00', 5, FALSE, 'SO-2024-0001', 'PO-AS-4101',  'AW000006', 6, 106, 106, 'International', 0, 0, 0, 0, '2024-01-08'),
  (5017, 1, '2024-02-19 00:00:00', '2024-02-12 13:00:00', 5, TRUE,  'SO-2024-0002', NULL,          'AW000007', 7, 107, 107, 'International', 0, 0, 0, 0, '2024-02-05'),
  (5018, 1, '2024-03-25 00:00:00', '2024-03-18 16:00:00', 5, FALSE, 'SO-2024-0003', 'PO-TC-5003',  'AW000008', 8, 108, 108, 'International', 0, 0, 0, 0, '2024-03-11'),
  (5019, 1, '2024-04-22 00:00:00', '2024-04-15 09:00:00', 5, TRUE,  'SO-2024-0004', NULL,          'AW000009', 9, 109, 109, 'International', 0, 0, 0, 0, '2024-04-08'),
  (5020, 1, '2024-05-20 00:00:00', '2024-05-13 12:00:00', 5, FALSE, 'SO-2024-0005', 'PO-AR-6105',  'AW000010', 10, 110, 110, 'Ground', 0, 0, 0, 0, '2024-05-06'),
  (5021, 1, '2024-06-24 00:00:00', '2024-06-17 10:00:00', 5, TRUE,  'SO-2024-0006', NULL,          'AW000001', 1, 101, 101, 'Ground', 0, 0, 0, 0, '2024-06-10'),
  (5022, 1, '2024-07-22 00:00:00', '2024-07-15 14:00:00', 5, FALSE, 'SO-2024-0007', 'PO-NW-2007',  'AW000002', 2, 111, 102, 'Freight', 0, 0, 0, 0, '2024-07-08'),
  (5023, 1, '2024-08-26 00:00:00', NULL,                  2, TRUE,  'SO-2024-0008', NULL,          'AW000003', 3, 103, 103, 'Express', 0, 0, 0, 0, '2024-08-12'),
  (5024, 1, '2024-09-23 00:00:00', NULL,                  2, FALSE, 'SO-2024-0009', 'PO-CC-3009',  'AW000004', 4, 112, 104, 'Freight', 0, 0, 0, 0, '2024-09-09');

INSERT INTO salesorderdetail
  (SalesOrderID, OrderQty, ProductID, UnitPrice, UnitPriceDIscount, LineTotal)
VALUES
  (5001, 1, 1001, 1299.00, 0.0000, ROUND(1 * 1299.00 * (1 - 0.0000), 2)),
  (5001, 2, 1301,   65.00, 0.0000, ROUND(2 *   65.00 * (1 - 0.0000), 2)),
  (5002, 3, 1005, 2199.00, 0.0500, ROUND(3 * 2199.00 * (1 - 0.0500), 2)),
  (5002, 6, 1202,   89.00, 0.1000, ROUND(6 *   89.00 * (1 - 0.1000), 2)),
  (5003, 1, 1004, 1099.00, 0.0000, ROUND(1 * 1099.00 * (1 - 0.0000), 2)),
  (5003, 1, 1302,  149.00, 0.0000, ROUND(1 *  149.00 * (1 - 0.0000), 2)),
  (5004, 2, 1003, 3499.00, 0.0800, ROUND(2 * 3499.00 * (1 - 0.0800), 2)),
  (5004, 4, 1101,  169.00, 0.0500, ROUND(4 *  169.00 * (1 - 0.0500), 2)),
  (5005, 1, 1002, 1899.00, 0.0000, ROUND(1 * 1899.00 * (1 - 0.0000), 2)),
  (5005, 2, 1203,   99.00, 0.0000, ROUND(2 *   99.00 * (1 - 0.0000), 2)),
  (5006, 2, 1006, 3999.00, 0.1000, ROUND(2 * 3999.00 * (1 - 0.1000), 2)),
  (5006, 4, 1302,  149.00, 0.1000, ROUND(4 *  149.00 * (1 - 0.1000), 2)),
  (5007, 1, 1001, 1299.00, 0.0000, ROUND(1 * 1299.00 * (1 - 0.0000), 2)),
  (5007, 3, 1201,   59.00, 0.0000, ROUND(3 *   59.00 * (1 - 0.0000), 2)),
  (5008, 2, 1005, 2199.00, 0.0400, ROUND(2 * 2199.00 * (1 - 0.0400), 2)),
  (5008, 2, 1103, 1099.00, 0.0500, ROUND(2 * 1099.00 * (1 - 0.0500), 2)),
  (5009, 1, 1004, 1099.00, 0.0000, ROUND(1 * 1099.00 * (1 - 0.0000), 2)),
  (5009, 5, 1303,   19.00, 0.0000, ROUND(5 *   19.00 * (1 - 0.0000), 2)),
  (5010, 4, 1002, 1899.00, 0.1200, ROUND(4 * 1899.00 * (1 - 0.1200), 2)),
  (5010, 8, 1301,   65.00, 0.1000, ROUND(8 *   65.00 * (1 - 0.1000), 2)),
  (5011, 1, 1006, 3999.00, 0.0000, ROUND(1 * 3999.00 * (1 - 0.0000), 2)),
  (5011, 1, 1103, 1099.00, 0.0000, ROUND(1 * 1099.00 * (1 - 0.0000), 2)),
  (5012, 2, 1003, 3499.00, 0.0700, ROUND(2 * 3499.00 * (1 - 0.0700), 2)),
  (5012, 6, 1203,   99.00, 0.1000, ROUND(6 *   99.00 * (1 - 0.1000), 2)),
  (5013, 1, 1001, 1299.00, 0.0000, ROUND(1 * 1299.00 * (1 - 0.0000), 2)),
  (5013, 2, 1202,   89.00, 0.0000, ROUND(2 *   89.00 * (1 - 0.0000), 2)),
  (5014, 3, 1005, 2199.00, 0.1000, ROUND(3 * 2199.00 * (1 - 0.1000), 2)),
  (5014, 3, 1102,  379.00, 0.0500, ROUND(3 *  379.00 * (1 - 0.0500), 2)),
  (5015, 1, 1004, 1099.00, 0.0000, ROUND(1 * 1099.00 * (1 - 0.0000), 2)),
  (5015, 2, 1302,  149.00, 0.0000, ROUND(2 *  149.00 * (1 - 0.0000), 2)),
  (5016, 2, 1002, 1899.00, 0.0600, ROUND(2 * 1899.00 * (1 - 0.0600), 2)),
  (5016, 5, 1201,   59.00, 0.1000, ROUND(5 *   59.00 * (1 - 0.1000), 2)),
  (5017, 1, 1003, 3499.00, 0.0000, ROUND(1 * 3499.00 * (1 - 0.0000), 2)),
  (5017, 1, 1301,   65.00, 0.0000, ROUND(1 *   65.00 * (1 - 0.0000), 2)),
  (5018, 2, 1006, 3999.00, 0.0800, ROUND(2 * 3999.00 * (1 - 0.0800), 2)),
  (5018, 2, 1103, 1099.00, 0.0800, ROUND(2 * 1099.00 * (1 - 0.0800), 2)),
  (5019, 1, 1001, 1299.00, 0.0000, ROUND(1 * 1299.00 * (1 - 0.0000), 2)),
  (5019, 4, 1303,   19.00, 0.0000, ROUND(4 *   19.00 * (1 - 0.0000), 2)),
  (5020, 3, 1005, 2199.00, 0.0900, ROUND(3 * 2199.00 * (1 - 0.0900), 2)),
  (5020, 6, 1202,   89.00, 0.1000, ROUND(6 *   89.00 * (1 - 0.1000), 2)),
  (5021, 1, 1004, 1099.00, 0.0000, ROUND(1 * 1099.00 * (1 - 0.0000), 2)),
  (5021, 2, 1203,   99.00, 0.0000, ROUND(2 *   99.00 * (1 - 0.0000), 2)),
  (5022, 2, 1003, 3499.00, 0.0500, ROUND(2 * 3499.00 * (1 - 0.0500), 2)),
  (5022, 5, 1101,  169.00, 0.1000, ROUND(5 *  169.00 * (1 - 0.1000), 2)),
  (5023, 1, 1002, 1899.00, 0.0000, ROUND(1 * 1899.00 * (1 - 0.0000), 2)),
  (5023, 2, 1302,  149.00, 0.0000, ROUND(2 *  149.00 * (1 - 0.0000), 2)),
  (5024, 2, 1005, 2199.00, 0.1000, ROUND(2 * 2199.00 * (1 - 0.1000), 2)),
  (5024, 4, 1102,  379.00, 0.1000, ROUND(4 *  379.00 * (1 - 0.1000), 2));

-- Derive order-level amounts from the line grain so every demo aggregate has a
-- checkable source. Freight varies by shipping method and tax is a fixed 8%.
UPDATE salesorderheader AS h
INNER JOIN (
  SELECT SalesOrderID, ROUND(SUM(LineTotal), 2) AS CalculatedSubTotal
  FROM salesorderdetail
  GROUP BY SalesOrderID
) AS d ON d.SalesOrderID = h.SalesOrderID
SET
  h.SubTotal = d.CalculatedSubTotal,
  h.TaxAmt = ROUND(d.CalculatedSubTotal * 0.08, 2),
  h.Freight = CASE h.ShipMethod
    WHEN 'Ground' THEN 18.00
    WHEN 'Express' THEN 32.00
    WHEN 'Freight' THEN 55.00
    WHEN 'International' THEN 75.00
    ELSE 25.00
  END,
  h.TotalDue = ROUND(
    d.CalculatedSubTotal
    + ROUND(d.CalculatedSubTotal * 0.08, 2)
    + CASE h.ShipMethod
        WHEN 'Ground' THEN 18.00
        WHEN 'Express' THEN 32.00
        WHEN 'Freight' THEN 55.00
        WHEN 'International' THEN 75.00
        ELSE 25.00
      END,
    2
  );

COMMIT;

