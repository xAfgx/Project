import { Product } from '../models';

export class ProductService {
  private products: Map<string, Product> = new Map();

  createProduct(productData: Omit<Product, 'id' | 'createdAt' | 'updatedAt'>): Product {
    const product: Product = {
      id: this.generateId(),
      ...productData,
      createdAt: new Date(),
      updatedAt: new Date()
    };

    this.products.set(product.id, product);
    return product;
  }

  getProduct(id: string): Product | undefined {
    return this.products.get(id);
  }

  getAllProducts(): Product[] {
    return Array.from(this.products.values());
  }

  updateProduct(id: string, updates: Partial<Product>): void {
    const product = this.getProduct(id);
    if (product) {
      Object.assign(product, updates, { updatedAt: new Date() });
    }
  }

  deleteProduct(id: string): boolean {
    return this.products.delete(id);
  }

  private generateId(): string {
    return Math.random().toString(36).substring(2, 15) + 
           Math.random().toString(36).substring(2, 15);
  }
}
