-- Ontology Data Agent - MySQL 8 demo schema
-- WARNING: this script drops and recreates all seven demo tables in the
-- dedicated ai_data_insight database. Do not run it against production data.

CREATE DATABASE IF NOT EXISTS ai_data_insight
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE ai_data_insight;

SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS salesorderdetail;
DROP TABLE IF EXISTS salesorderheader;
DROP TABLE IF EXISTS salescustomeraddress;
DROP TABLE IF EXISTS salesproduct;
DROP TABLE IF EXISTS salesproductcategory;
DROP TABLE IF EXISTS salesaddress;
DROP TABLE IF EXISTS salescustomer;
SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE salescustomer (
  CustomerID INT NOT NULL,
  Title VARCHAR(20) NULL,
  FirstName VARCHAR(80) NOT NULL,
  MiddleName VARCHAR(80) NULL,
  LastName VARCHAR(80) NOT NULL,
  Suffix VARCHAR(20) NULL,
  CompanyName VARCHAR(160) NULL,
  EmailAddress VARCHAR(160) NOT NULL,
  Phone VARCHAR(40) NULL,
  PRIMARY KEY (CustomerID),
  UNIQUE KEY uq_salescustomer_email (EmailAddress),
  KEY ix_salescustomer_company (CompanyName)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='AdventureWorks-style demo customers; one row per customer';

CREATE TABLE salesaddress (
  AddressID INT NOT NULL,
  AddressLine1 VARCHAR(160) NOT NULL,
  AddressLine2 VARCHAR(160) NULL,
  City VARCHAR(80) NOT NULL,
  StateProvince VARCHAR(80) NOT NULL,
  CountryRegion VARCHAR(80) NOT NULL,
  PostalCode VARCHAR(20) NOT NULL,
  PRIMARY KEY (AddressID),
  KEY ix_salesaddress_region (CountryRegion, StateProvince, City)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Demo postal addresses; one row per address';

CREATE TABLE salescustomeraddress (
  CustomerID INT NOT NULL,
  AddressID INT NOT NULL,
  AddressType VARCHAR(20) NOT NULL,
  PRIMARY KEY (CustomerID, AddressID, AddressType),
  KEY ix_salescustomeraddress_address (AddressID),
  CONSTRAINT fk_customeraddress_customer
    FOREIGN KEY (CustomerID) REFERENCES salescustomer (CustomerID),
  CONSTRAINT fk_customeraddress_address
    FOREIGN KEY (AddressID) REFERENCES salesaddress (AddressID),
  CONSTRAINT ck_customeraddress_type
    CHECK (AddressType IN ('Billing', 'Shipping', 'Main Office'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Customer-address roles; one row per customer/address/role';

CREATE TABLE salesproductcategory (
  ProductCategoryID INT NOT NULL,
  ParentProductCategoryID INT NULL,
  Name VARCHAR(100) NOT NULL,
  PRIMARY KEY (ProductCategoryID),
  UNIQUE KEY uq_productcategory_parent_name (ParentProductCategoryID, Name),
  KEY ix_productcategory_parent (ParentProductCategoryID),
  CONSTRAINT fk_productcategory_parent
    FOREIGN KEY (ParentProductCategoryID)
    REFERENCES salesproductcategory (ProductCategoryID)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Recursive two-level demo product category hierarchy';

CREATE TABLE salesproduct (
  ProductID INT NOT NULL,
  Name VARCHAR(160) NOT NULL,
  ProductNumber VARCHAR(50) NOT NULL,
  Color VARCHAR(30) NULL,
  StandardCost DECIMAL(12,2) NOT NULL,
  ListPrice DECIMAL(12,2) NOT NULL,
  Size VARCHAR(20) NULL,
  Weight DECIMAL(10,2) NULL,
  ProductCategoryID INT NOT NULL,
  PRIMARY KEY (ProductID),
  UNIQUE KEY uq_salesproduct_number (ProductNumber),
  KEY ix_salesproduct_category (ProductCategoryID),
  KEY ix_salesproduct_color (Color),
  CONSTRAINT fk_salesproduct_category
    FOREIGN KEY (ProductCategoryID)
    REFERENCES salesproductcategory (ProductCategoryID),
  CONSTRAINT ck_salesproduct_cost CHECK (StandardCost >= 0),
  CONSTRAINT ck_salesproduct_price CHECK (ListPrice >= 0),
  CONSTRAINT ck_salesproduct_weight CHECK (Weight IS NULL OR Weight >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Sellable demo products; monetary values are USD and weight is grams';

CREATE TABLE salesorderheader (
  SalesOrderID INT NOT NULL,
  RevisionNumber INT NOT NULL DEFAULT 0,
  DueDate DATETIME NOT NULL,
  ShipDate DATETIME NULL,
  Status INT NOT NULL,
  OnlineOrderFlag BOOLEAN NOT NULL,
  SalesOrderNumber VARCHAR(30) NOT NULL,
  PurchaseOrderNumber VARCHAR(30) NULL,
  AccountNumber VARCHAR(30) NOT NULL,
  CustomerID INT NOT NULL,
  ShipToAddressID INT NOT NULL,
  BillToAddressID INT NOT NULL,
  ShipMethod VARCHAR(80) NOT NULL,
  SubTotal DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  TaxAmt DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  Freight DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  TotalDue DECIMAL(14,2) NOT NULL DEFAULT 0.00,
  OrderDate DATE NOT NULL,
  PRIMARY KEY (SalesOrderID),
  UNIQUE KEY uq_salesorderheader_number (SalesOrderNumber),
  KEY ix_salesorderheader_date (OrderDate),
  KEY ix_salesorderheader_customer_date (CustomerID, OrderDate),
  KEY ix_salesorderheader_ship_address (ShipToAddressID),
  KEY ix_salesorderheader_bill_address (BillToAddressID),
  CONSTRAINT fk_salesorderheader_customer
    FOREIGN KEY (CustomerID) REFERENCES salescustomer (CustomerID),
  CONSTRAINT fk_salesorderheader_ship_address
    FOREIGN KEY (ShipToAddressID) REFERENCES salesaddress (AddressID),
  CONSTRAINT fk_salesorderheader_bill_address
    FOREIGN KEY (BillToAddressID) REFERENCES salesaddress (AddressID),
  CONSTRAINT ck_salesorderheader_status CHECK (Status BETWEEN 1 AND 6),
  CONSTRAINT ck_salesorderheader_dates
    CHECK (DueDate >= OrderDate AND (ShipDate IS NULL OR ShipDate >= OrderDate)),
  CONSTRAINT ck_salesorderheader_amounts
    CHECK (SubTotal >= 0 AND TaxAmt >= 0 AND Freight >= 0 AND TotalDue >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Sales-order headers; monetary values are USD';

CREATE TABLE salesorderdetail (
  SalesOrderID INT NOT NULL,
  OrderQty INT NOT NULL,
  ProductID INT NOT NULL,
  UnitPrice DECIMAL(12,2) NOT NULL,
  UnitPriceDIscount DECIMAL(6,4) NOT NULL DEFAULT 0.0000,
  LineTotal DECIMAL(14,2) NOT NULL,
  PRIMARY KEY (SalesOrderID, ProductID),
  KEY ix_salesorderdetail_product (ProductID),
  CONSTRAINT fk_salesorderdetail_order
    FOREIGN KEY (SalesOrderID) REFERENCES salesorderheader (SalesOrderID),
  CONSTRAINT fk_salesorderdetail_product
    FOREIGN KEY (ProductID) REFERENCES salesproduct (ProductID),
  CONSTRAINT ck_salesorderdetail_quantity CHECK (OrderQty > 0),
  CONSTRAINT ck_salesorderdetail_price CHECK (UnitPrice >= 0),
  CONSTRAINT ck_salesorderdetail_discount
    CHECK (UnitPriceDIscount >= 0 AND UnitPriceDIscount <= 1),
  CONSTRAINT ck_salesorderdetail_total
    CHECK (LineTotal = ROUND(OrderQty * UnitPrice * (1 - UnitPriceDIscount), 2))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Product lines in sales orders; monetary values are USD';
