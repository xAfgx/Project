import { Shop } from '../models';

export class ShopService {
  private shops: Map<string, Shop> = new Map();

  createShop(shopData: Omit<Shop, 'id' | 'createdAt' | 'updatedAt'>): Shop {
    const shop: Shop = {
      id: this.generateId(),
      ...shopData,
      createdAt: new Date(),
      updatedAt: new Date()
    };

    this.shops.set(shop.id, shop);
    return shop;
  }

  getShop(id: string): Shop | undefined {
    return this.shops.get(id);
  }

  getAllShops(): Shop[] {
    return Array.from(this.shops.values());
  }

  updateShop(id: string, updates: Partial<Shop>): void {
    const shop = this.getShop(id);
    if (shop) {
      Object.assign(shop, updates, { updatedAt: new Date() });
    }
  }

  deleteShop(id: string): boolean {
    return this.shops.delete(id);
  }

  private generateId(): string {
    return Math.random().toString(36).substring(2, 15) + 
           Math.random().toString(36).substring(2, 15);
  }
}
